""" Views for interacting with the payment processor. """
from __future__ import unicode_literals

import logging
import os
from cStringIO import StringIO

import six
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.core.management import call_command
from django.core.urlresolvers import reverse
from django.db import transaction
from django.http import JsonResponse, Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView, TemplateView, View
from oscar.apps.partner import strategy
from oscar.apps.payment.exceptions import PaymentError, UserCancelled, TransactionDeclined
from oscar.core.loading import get_class, get_model

from ecommerce.extensions.checkout.mixins import EdxOrderPlacementMixin
from ecommerce.extensions.checkout.utils import get_receipt_page_url
from ecommerce.extensions.payment.exceptions import InvalidSignatureError
from ecommerce.extensions.payment.forms import PaymentForm
from ecommerce.extensions.payment.processors.cybersource import Cybersource
from ecommerce.extensions.payment.processors.paypal import Paypal

logger = logging.getLogger(__name__)

Applicator = get_class('offer.utils', 'Applicator')
Basket = get_model('basket', 'Basket')
BillingAddress = get_model('order', 'BillingAddress')
Country = get_model('address', 'Country')
NoShippingRequired = get_class('shipping.methods', 'NoShippingRequired')
Order = get_model('order', 'Order')
OrderNumberGenerator = get_class('order.utils', 'OrderNumberGenerator')
OrderTotalCalculator = get_class('checkout.calculators', 'OrderTotalCalculator')
PaymentProcessorResponse = get_model('payment', 'PaymentProcessorResponse')


class CybersourceSubmitView(FormView):
    """ Starts CyberSource payment process.

    This view is intended to be called asynchronously by the payment form. The view expects POST data containing a
    `Basket` ID. The specified basket is frozen, and CyberSource parameters are returned as a JSON object.
    """
    FIELD_MAPPINGS = {
        'city': 'bill_to_address_city',
        'country': 'bill_to_address_country',
        'address_line1': 'bill_to_address_line1',
        'address_line2': 'bill_to_address_line2',
        'postal_code': 'bill_to_address_postal_code',
        'state': 'bill_to_address_state',
        'first_name': 'bill_to_forename',
        'last_name': 'bill_to_surname',
    }
    form_class = PaymentForm
    http_method_names = ['post', 'options']

    @method_decorator(login_required)
    def dispatch(self, request, *args, **kwargs):
        return super(CybersourceSubmitView, self).dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super(CybersourceSubmitView, self).get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def _basket_error_response(self, error_msg):
        data = {
            'error': error_msg,
        }
        return JsonResponse(data, status=400)

    def form_invalid(self, form):
        errors = {field: error[0] for field, error in form.errors.iteritems()}
        logger.debug(errors)

        if errors.get('basket'):
            error_msg = _('There was a problem retrieving your basket. Refresh the page to try again.')
            return self._basket_error_response(error_msg)

        return JsonResponse({'field_errors': errors}, status=400)

    def form_valid(self, form):
        data = form.cleaned_data
        basket = data['basket']
        request = self.request
        user = request.user

        # Ensure we aren't attempting to purchase a basket that has already been purchased, frozen,
        # or merged with another basket.
        if basket.status != Basket.OPEN:
            logger.debug('Basket %d must be in the "Open" state. It is currently in the "%s" state.',
                         basket.id, basket.status)
            error_msg = _('Your basket may have been modified or already purchased. Refresh the page to try again.')
            return self._basket_error_response(error_msg)

        basket.strategy = request.strategy
        Applicator().apply(basket, user, self.request)

        # Add extra parameters for Silent Order POST
        extra_parameters = {
            'payment_method': 'card',
            'unsigned_field_names': ','.join(Cybersource.PCI_FIELDS),
            'bill_to_email': user.email,
            'device_fingerprint_id': request.session.session_key,
        }

        for source, destination in six.iteritems(self.FIELD_MAPPINGS):
            extra_parameters[destination] = data[source]

        parameters = Cybersource(self.request.site).get_transaction_parameters(
            basket,
            use_client_side_checkout=True,
            extra_parameters=extra_parameters
        )

        # This parameter is only used by the Web/Mobile flow. It is not needed for for Silent Order POST.
        parameters.pop('payment_page_url', None)

        # Ensure that the response can be properly rendered so that we
        # don't have to deal with thawing the basket in the event of an error.
        response = JsonResponse({'form_fields': parameters})

        # Freeze the basket since the user is paying for it now.
        basket.freeze()

        return response


class CybersourceNotifyView(EdxOrderPlacementMixin, View):
    """ Validates a response from CyberSource and processes the associated basket/order appropriately. """

    @property
    def payment_processor(self):
        return Cybersource(self.request.site)

    # Disable atomicity for the view. Otherwise, we'd be unable to commit to the database
    # until the request had concluded; Django will refuse to commit when an atomic() block
    # is active, since that would break atomicity. Without an order present in the database
    # at the time fulfillment is attempted, asynchronous order fulfillment tasks will fail.
    @method_decorator(transaction.non_atomic_requests)
    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super(CybersourceNotifyView, self).dispatch(request, *args, **kwargs)

    def _get_billing_address(self, cybersource_response):
        return BillingAddress(
            first_name=cybersource_response['req_bill_to_forename'],
            last_name=cybersource_response['req_bill_to_surname'],
            line1=cybersource_response['req_bill_to_address_line1'],

            # Address line 2 is optional
            line2=cybersource_response.get('req_bill_to_address_line2', ''),

            # Oscar uses line4 for city
            line4=cybersource_response['req_bill_to_address_city'],
            postcode=cybersource_response['req_bill_to_address_postal_code'],
            # State is optional
            state=cybersource_response.get('req_bill_to_address_state', ''),
            country=Country.objects.get(
                iso_3166_1_a2=cybersource_response['req_bill_to_address_country']))

    def _get_basket(self, basket_id):
        if not basket_id:
            return None

        try:
            basket_id = int(basket_id)
            basket = Basket.objects.get(id=basket_id)
            basket.strategy = strategy.Default()
            Applicator().apply(basket, basket.owner, self.request)
            return basket
        except (ValueError, ObjectDoesNotExist):
            return None

    def post(self, request):
        """Process a CyberSource merchant notification and place an order for paid products as appropriate."""

        # Note (CCB): Orders should not be created until the payment processor has validated the response's signature.
        # This validation is performed in the handle_payment method. After that method succeeds, the response can be
        # safely assumed to have originated from CyberSource.
        cybersource_response = request.POST.dict()
        basket = None
        transaction_id = None

        try:
            transaction_id = cybersource_response.get('transaction_id')
            order_number = cybersource_response.get('req_reference_number')
            basket_id = OrderNumberGenerator().basket_id(order_number)

            logger.info(
                'Received CyberSource merchant notification for transaction [%s], associated with basket [%d].',
                transaction_id,
                basket_id
            )

            basket = self._get_basket(basket_id)

            if not basket:
                logger.error('Received payment for non-existent basket [%s].', basket_id)
                return HttpResponse(status=400)
        finally:
            # Store the response in the database regardless of its authenticity.
            ppr = self.payment_processor.record_processor_response(cybersource_response, transaction_id=transaction_id,
                                                                   basket=basket)

        try:
            # Explicitly delimit operations which will be rolled back if an exception occurs.
            with transaction.atomic():
                try:
                    self.handle_payment(cybersource_response, basket)
                except InvalidSignatureError:
                    logger.exception(
                        'Received an invalid CyberSource response. The payment response was recorded in entry [%d].',
                        ppr.id
                    )
                    return HttpResponse(status=400)
                except (UserCancelled, TransactionDeclined) as exception:
                    logger.info(
                        'CyberSource payment did not complete for basket [%d] because [%s]. '
                        'The payment response was recorded in entry [%d].',
                        basket.id,
                        exception.__class__.__name__,
                        ppr.id
                    )
                    return HttpResponse()
                except PaymentError:
                    logger.exception(
                        'CyberSource payment failed for basket [%d]. The payment response was recorded in entry [%d].',
                        basket.id,
                        ppr.id
                    )
                    return HttpResponse()
        except:  # pylint: disable=bare-except
            logger.exception('Attempts to handle payment for basket [%d] failed.', basket.id)
            return HttpResponse(status=500)

        try:
            # Note (CCB): In the future, if we do end up shipping physical products, we will need to
            # properly implement shipping methods. For more, see
            # http://django-oscar.readthedocs.org/en/latest/howto/how_to_configure_shipping.html.
            shipping_method = NoShippingRequired()
            shipping_charge = shipping_method.calculate(basket)

            # Note (CCB): This calculation assumes the payment processor has not sent a partial authorization,
            # thus we use the amounts stored in the database rather than those received from the payment processor.
            order_total = OrderTotalCalculator().calculate(basket, shipping_charge)
            billing_address = self._get_billing_address(cybersource_response)

            user = basket.owner

            self.handle_order_placement(
                order_number,
                user,
                basket,
                None,
                shipping_method,
                shipping_charge,
                billing_address,
                order_total,
                request=request
            )

            return HttpResponse()
        except:  # pylint: disable=bare-except
            logger.exception(self.order_placement_failure_msg, basket.id)
            return HttpResponse(status=500)


class CybersourceInterstitialView(CybersourceNotifyView, TemplateView):
    """ Interstitial view for Cybersource Payments. """
    template_name = 'checkout/cybersource_error.html'

    def post(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        # CyberSource responses will indicate whether a payment failed due to a transaction on their end. In this case,
        # we can provide the learner more detailed information in the error message.
        cybersource_response = request.POST.dict()
        order_number = cybersource_response['req_reference_number']
        if Order.objects.filter(number=order_number).exists():
            receipt_url = get_receipt_page_url(
                order_number=cybersource_response.get('req_reference_number'),
                site_configuration=self.request.site.siteconfiguration
            )
            return redirect(receipt_url)
        else:
            context = self.get_context_data()
            basket_id = OrderNumberGenerator().basket_id(order_number)
            basket = self._get_basket(basket_id)
            if basket:
                basket.thaw()
            return self.render_to_response(context=context, status=500)

    def get_context_data(self, **kwargs):
        context = super(CybersourceInterstitialView, self).get_context_data(**kwargs)
        context.update({
            'basket_url': self.request.site.siteconfiguration.build_ecommerce_url(reverse('basket:summary')),
            'payment_support_email': self.request.site.siteconfiguration.payment_support_email
        })
        return context


class PaypalPaymentExecutionView(EdxOrderPlacementMixin, View):
    """Execute an approved PayPal payment and place an order for paid products as appropriate."""

    @property
    def payment_processor(self):
        return Paypal(self.request.site)

    # Disable atomicity for the view. Otherwise, we'd be unable to commit to the database
    # until the request had concluded; Django will refuse to commit when an atomic() block
    # is active, since that would break atomicity. Without an order present in the database
    # at the time fulfillment is attempted, asynchronous order fulfillment tasks will fail.
    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request, *args, **kwargs):
        return super(PaypalPaymentExecutionView, self).dispatch(request, *args, **kwargs)

    def _get_basket(self, payment_id):
        """
        Retrieve a basket using a payment ID.

        Arguments:
            payment_id: payment_id received from PayPal.

        Returns:
            It will return related basket or log exception and return None if
            duplicate payment_id received or any other exception occurred.

        """
        try:
            basket = PaymentProcessorResponse.objects.get(
                processor_name=self.payment_processor.NAME,
                transaction_id=payment_id
            ).basket
            basket.strategy = strategy.Default()
            Applicator().apply(basket, basket.owner, self.request)
            return basket
        except MultipleObjectsReturned:
            logger.exception(u"Duplicate payment ID [%s] received from PayPal.", payment_id)
            return None
        except Exception:  # pylint: disable=broad-except
            logger.exception(u"Unexpected error during basket retrieval while executing PayPal payment.")
            return None

    def get(self, request):
        """Handle an incoming user returned to us by PayPal after approving payment."""
        payment_id = request.GET.get('paymentId')
        payer_id = request.GET.get('PayerID')
        logger.info(u"Payment [%s] approved by payer [%s]", payment_id, payer_id)

        paypal_response = request.GET.dict()
        basket = self._get_basket(payment_id)

        if not basket:
            return redirect(self.payment_processor.error_url)

        receipt_url = get_receipt_page_url(
            order_number=basket.order_number,
            site_configuration=basket.site.siteconfiguration
        )

        try:
            with transaction.atomic():
                try:
                    self.handle_payment(paypal_response, basket)
                except PaymentError:
                    return redirect(self.payment_processor.error_url)
        except:  # pylint: disable=bare-except
            logger.exception('Attempts to handle payment for basket [%d] failed.', basket.id)
            return redirect(receipt_url)

        try:
            shipping_method = NoShippingRequired()
            shipping_charge = shipping_method.calculate(basket)
            order_total = OrderTotalCalculator().calculate(basket, shipping_charge)

            user = basket.owner
            # Given a basket, order number generation is idempotent. Although we've already
            # generated this order number once before, it's faster to generate it again
            # than to retrieve an invoice number from PayPal.
            order_number = basket.order_number

            self.handle_order_placement(
                order_number=order_number,
                user=user,
                basket=basket,
                shipping_address=None,
                shipping_method=shipping_method,
                shipping_charge=shipping_charge,
                billing_address=None,
                order_total=order_total,
                request=request
            )

            return redirect(receipt_url)
        except:  # pylint: disable=bare-except
            logger.exception(self.order_placement_failure_msg, basket.id)
            return redirect(receipt_url)


class PaypalProfileAdminView(View):
    ACTIONS = ('list', 'create', 'show', 'update', 'delete', 'enable', 'disable')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise Http404

        return super(PaypalProfileAdminView, self).dispatch(request, *args, **kwargs)

    def get(self, request, *_args, **_kwargs):

        # Capture all output and logging
        out = StringIO()
        err = StringIO()
        log = StringIO()

        log_handler = logging.StreamHandler(log)
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        log_handler.setFormatter(formatter)
        logger.addHandler(log_handler)

        action = request.GET.get('action')
        if action not in self.ACTIONS:
            return HttpResponseBadRequest("Invalid action.")
        profile_id = request.GET.get('id', '')
        json_str = request.GET.get('json', '')

        command_params = [action]
        if action in ('show', 'update', 'delete', 'enable', 'disable'):
            command_params.append(profile_id.strip())
        if action in ('create', 'update'):
            command_params.append(json_str.strip())

        logger.info("user %s is managing paypal profiles: %s", request.user.username, command_params)

        success = False
        try:
            call_command('paypal_profile', *command_params,
                         settings=os.environ['DJANGO_SETTINGS_MODULE'], stdout=out, stderr=err)
            success = True
        except:  # pylint: disable=bare-except
            # we still want to present the output whether or not the command succeeded.
            pass

        # Format the output for display
        output = u'STDOUT\n{out}\n\nSTDERR\n{err}\n\nLOG\n{log}'.format(out=out.getvalue(), err=err.getvalue(),
                                                                        log=log.getvalue())

        # Remove the log capture handler
        logger.removeHandler(log_handler)

        return HttpResponse(output, content_type='text/plain', status=200 if success else 500)
