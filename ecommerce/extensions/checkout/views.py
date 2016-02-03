import base64
import binascii
import datetime
import hashlib
import hmac
from collections import OrderedDict
from urlparse import urljoin

import requests
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView
from oscar.apps.checkout.views import *  # pylint: disable=wildcard-import, unused-wildcard-import
from oscar.apps.payment import models

from ecommerce.extensions.payment.helpers import get_processor_class


class PaymentView(TemplateView):
    """
    Checkout payment view.

    This view merges the existing payment details, preview, and confirmation views into one. The user will see the
    contents of the basket along with the payment UI needed to place the order.
    """
    template_name = 'checkout/payment.html'

    @method_decorator(ensure_csrf_cookie)
    def dispatch(self, *args, **kwargs):
        return super(PaymentView, self).dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(PaymentView, self).get_context_data(**kwargs)
        basket = Basket.get_basket(self.request.user, self.request.site)

        context.update({
            'basket': basket,
            'payment_processors': self.get_payment_processors()
        })
        return context

    def get_payment_processors(self):
        """ Retrieve the list of active payment processors. """
        # TODO Retrieve this information from SiteConfiguration
        return [get_processor_class(path) for path in settings.PAYMENT_PROCESSORS]


class AdyenPaymentDetailsView(PaymentDetailsView):
    processor_settings = None

    def dispatch(self, request, *args, **kwargs):
        self.processor_settings = settings.PAYMENT_PROCESSOR_CONFIG['adyen']
        return super(AdyenPaymentDetailsView, self).dispatch(request, *args, **kwargs)

    def escapeVal(self, val):
        return unicode(val).replace('\\', '\\\\').replace(':', '\\:')

    def generate_adyen_signature(self, fields, signing_key):
        fields = OrderedDict(sorted(fields.items(), key=lambda t: t[0]))
        signing_string = ':'.join(map(self.escapeVal, fields.keys() + fields.values()))
        signing_key = binascii.a2b_hex(signing_key)
        hm = hmac.new(signing_key, signing_string, hashlib.sha256)
        return base64.b64encode(hm.digest())

    def get_context_data(self, **kwargs):
        context = super(AdyenPaymentDetailsView, self).get_context_data(**kwargs)
        basket = self.request.basket
        session_and_shipment_deadline = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()
        fields = {
            'merchantReference': basket.order_number,
            'paymentAmount': self.format_decimal_for_adyen(basket.total_incl_tax),
            'currencyCode': basket.currency,
            'shipBeforeDate': session_and_shipment_deadline,
            'skinCode': self.processor_settings['hpp_skin_code'],
            'merchantAccount': self.processor_settings['merchant_account'],
            'sessionValidity': session_and_shipment_deadline,
            'shopperEmail': basket.owner.email,
            'shopperReference': basket.owner.username,
        }
        fields['merchantSig'] = self.generate_adyen_signature(fields, self.processor_settings['hpp_signing_key'])

        context.update({
            'adyen_cse_url': self.processor_settings['cse_url'],
            'adyen_hpp_url': self.processor_settings['hpp_url'],
            'adyen_hpp_fields': fields
        })
        return context

    def handle_payment_details_submission(self, request):
        adyen_data = request.POST.get('adyen-encrypted-data')

        if not adyen_data:
            return self.render_payment_details(request, error='Payment invalid')

        return self.render_preview(request)

    def build_submission(self, **kwargs):
        submission = super(AdyenPaymentDetailsView, self).build_submission(**kwargs)
        submission['payment_kwargs'].update({
            'adyen_data': self.request.POST.get('adyen-encrypted-data'),
            'basket': self.request.basket,
            'ip_address': self.request.META.get('HTTP_X_FORWARDED_FOR') or self.request.META.get('REMOTE_ADDR')
        })
        return submission

    def format_decimal_for_adyen(self, value):
        """
        Formats a Decimal price value for submission to Adyen.

        Arguments:
            value (Decimal) - Value to be converted.

        Returns:
            int
        """
        # TODO Convert based on currency: https://docs.adyen.com/manuals/api-manual/api-currency-codes.
        return int(value * 100)

    def handle_payment(self, order_number, total, **kwargs):
        api_host = self.processor_settings['api_host']
        merchant_account = self.processor_settings['merchant_account']
        username = self.processor_settings['username']
        password = self.processor_settings['password']
        auth = requests.auth.HTTPBasicAuth(username, password)

        basket = kwargs['basket']
        encrypted_card = kwargs['adyen_data']
        ip_address = kwargs['ip_address']
        total_incl_tax = total.incl_tax
        amount = {
            'value': self.format_decimal_for_adyen(total_incl_tax),
            'currency': total.currency
        }

        # Authorize: https://docs.adyen.com/manuals/api-manual/payment-responses
        data = {
            'merchantAccount': merchant_account,
            'additionalData': {
                'card.encrypted.json': encrypted_card
            },
            'shopperIP': ip_address,
            'shopperEmail': basket.owner.email,
            'shopperReference': basket.owner.username,
            'amount': amount,
            'reference': order_number
        }

        response = requests.post(urljoin(api_host, '/pal/servlet/Payment/v12/authorise'), auth=auth, json=data)

        if not response.ok:
            raise PaymentError(response.text)

        psp_reference = response.json()['pspReference']

        # TODO Investigate automatic capture settings in customer area.
        # Capture: https://docs.adyen.com/manuals/api-manual/modification-requests/capture
        data = {
            'merchantAccount': merchant_account,
            'modificationAmount': amount,
            'originalReference': psp_reference,
        }
        response = requests.post(urljoin(api_host, '/pal/servlet/Payment/v12/capture'), auth=auth, json=data)

        # Record payment source and event
        source_type, __ = models.SourceType.objects.get_or_create(name='Adyen')
        source = source_type.sources.model(source_type=source_type,
                                           amount_allocated=total_incl_tax,
                                           currency=total.currency,
                                           reference=psp_reference)
        source.create_deferred_transaction(models.Transaction.DEBIT, total_incl_tax, psp_reference, 'Success')

        self.add_payment_source(source)
        self.add_payment_event('Settled', total_incl_tax, psp_reference)
