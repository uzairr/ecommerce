import braintree
from django.conf import settings
from oscar.apps.checkout.views import *  # pylint: disable=wildcard-import, unused-wildcard-import

from ecommerce.extensions.order.constants import PaymentEventTypeName

SourceType = get_model('payment', 'SourceType')


class ExtendedPaymentDetailsView(PaymentDetailsView):
    def dispatch(self, request, *args, **kwargs):
        self.configure_braintree()
        return super(ExtendedPaymentDetailsView, self).dispatch(request, *args, **kwargs)

    def configure_braintree(self):
        braintree_settings = settings.PAYMENT_PROCESSOR_CONFIG['braintree']
        braintree.Configuration.configure(
            braintree.Environment.Sandbox,
            merchant_id=braintree_settings['merchant_id'],
            public_key=braintree_settings['public_key'],
            private_key=braintree_settings['private_key']
        )

    def get_context_data(self, **kwargs):
        context = super(ExtendedPaymentDetailsView, self).get_context_data(**kwargs)
        context['braintree_token'] = self.generate_braintree_client_token(self.request.user)
        return context

    def generate_braintree_client_token(self, user):
        username = user.get_username()
        braintree.Customer.create({
            'id': username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'custom_fields': {
                'full_name': user.get_full_name()
            }
        })
        return braintree.ClientToken.generate({'customer_id': username})

    def handle_payment(self, order_number, total, **kwargs):
        result = braintree.Transaction.sale({
            'amount': total.incl_tax,
            'customer_id': kwargs['user'].get_username(),
            'options': {
                'submit_for_settlement': True,
                'store_in_vault_on_success': True,
            },
            'order_id': order_number,
            'payment_method_nonce': kwargs['braintree_nonce'],
        })

        if result.is_success:
            # Record payment source and event
            source_type, __ = SourceType.objects.get_or_create(name='Braintree')
            source = source_type.sources.model(
                source_type=source_type,
                amount_allocated=total.incl_tax,
                amount_debited=total.incl_tax,
                currency=total.currency)
            self.add_payment_source(source)
            self.add_payment_event(PaymentEventTypeName.PAID, total.incl_tax)
        else:
            transaction = result.transaction
            msg = 'Braintree transaction failed: [{code}] - [{text}]'.format(
                code=transaction.processor_response_code,
                text=transaction.processor_response_text)
            raise PaymentError(msg)

    def build_submission(self, **kwargs):
        submission = super(ExtendedPaymentDetailsView, self).build_submission(**kwargs)
        submission['payment_kwargs'] = {
            'braintree_nonce': self.request.POST.get('braintree_nonce'),
            'user': self.request.user,
        }
        return submission
