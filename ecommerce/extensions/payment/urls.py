""" Payment-related URLs """
from django.conf.urls import url, include

from ecommerce.extensions.payment import views

PAYPAL_URLS = [
    url(r'^execute/$', views.PaypalPaymentExecutionView.as_view(), name='execute'),
    url(r'^profiles/$', views.PaypalProfileAdminView.as_view(), name='profiles'),
    url(r'^webhooks/dispute/$', views.PaypalDisputeWebhookView.as_view(), name='webhooks_dispute'),
]

urlpatterns = [
    url(r'^cybersource/notify/$', views.CybersourceNotifyView.as_view(), name='cybersource_notify'),
    url(r'^paypal/', include(PAYPAL_URLS, namespace='paypal')),
]
