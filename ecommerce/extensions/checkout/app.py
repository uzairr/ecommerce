from oscar.apps.checkout import app

from ecommerce.extensions.checkout.views import ExtendedPaymentDetailsView


class CheckoutApplication(app.CheckoutApplication):
    payment_details_view = ExtendedPaymentDetailsView


application = CheckoutApplication()
