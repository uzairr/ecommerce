from __future__ import unicode_literals

import logging

import pycountry
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext as _
from oscar.core.loading import get_model

logger = logging.getLogger(__name__)
Basket = get_model('basket', 'Basket')


def country_choices():
    """ Returns a tuple of tuples, each containing an ISO 3166 country code. """
    return ((country.alpha2, country.name) for country in pycountry.countries)


class PaymentForm(forms.Form):
    """
    Payment form with billing details.

    This form captures the data necessary to complete a payment transaction. The current field constraints pertain
    to CyberSource Silent Order POST, but should work nicely with other payment providers.
    """

    def __init__(self, user, *args, **kwargs):
        super(PaymentForm, self).__init__(*args, **kwargs)
        self.fields['basket'].queryset = self.fields['basket'].queryset.filter(owner=user)

    basket = forms.ModelChoiceField(queryset=Basket.objects.all(), widget=forms.HiddenInput())
    first_name = forms.CharField(max_length=60, label=_('First Name'))
    last_name = forms.CharField(max_length=60, label=_('Last Name'))
    address_line1 = forms.CharField(max_length=29, label=_('Address'))
    address_line2 = forms.CharField(max_length=29, required=False, label=_('Address (continued)'))
    city = forms.CharField(max_length=32, label=_('City'))
    state = forms.CharField(max_length=60, required=False, label=_('State/Province'))
    postal_code = forms.CharField(max_length=10, required=False, label=_('Zip/Postal Code'))
    country = forms.ChoiceField(choices=country_choices, label=_('Country'))

    def clean(self):
        cleaned_data = super(PaymentForm, self).clean()

        # Perform specific validation for the United States and Canada
        country = cleaned_data.get('country')
        if country in ('US', 'CA'):
            state = cleaned_data.get('state')

            # Ensure that a valid 2-character state/province code is specified.
            if not state:
                raise ValidationError({'state': _('This field is required.')})

            code = '{country}-{state}'.format(country=country, state=state)

            try:
                pycountry.subdivisions.get(code=code)
            except KeyError:
                msg = _('{state} is not a valid state/province in {country}.').format(state=state, country=country)
                logger.debug(msg)
                raise ValidationError({'state': msg})

            # Ensure the postal code is present, and limited to 9 characters
            postal_code = cleaned_data.get('postal_code')
            if not postal_code:
                raise ValidationError({'postal_code': _('This field is required.')})

            if len(postal_code) > 9:
                raise ValidationError(
                    {'postal_code': _(
                        'Postal codes for the U.S. and Canada are limited to nine (9) characters.')})

        return cleaned_data
