# noinspection PyUnresolvedReferences
import hashlib
import re

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from oscar.apps.offer.abstract_models import AbstractConditionalOffer, AbstractRange
from threadlocals.threadlocals import get_current_request


class ConditionalOffer(AbstractConditionalOffer):
    email_domains = models.CharField(max_length=255, blank=True, null=True)

    def is_email_valid(self, email):
        """
        Check if the email is within the email_domains if email_domains are set,
        else return True. If there is a domain with a sub domain in the list of
        valid email domains then the user's email needs to match exactly the
        domain and sub domain. If there is only a domain (without sub domains) in
        the list of valid email domains then the user's domain needs to match
        regardless of the subdomain.

        Examples:

            1)
                email_domains value: 'example.com'
                valid user email domains:
                    'example.com', 'sub1.example.com', 'sub2.example.com' etc.
                invalid user email domains:
                    'other.com' etc.

            2)
                email_domains value: 'sub.example.com'
                valid user email domain:
                    'sub.example.com'
                invalid user email domains:
                    'sub1.example.com', 'example.com' etc.

        Args:
            email (str): Email of the user.

        Returns:
            True if the email is valid or when there are no valid email domains set,
            False otherwise.
        """
        if self.email_domains:
            for domain in self.email_domains.split(','):
                pattern = r'(?P<username>.+)@(?P<subdomain>\w+\.)*{domain}'.format(domain=domain)
                match = re.match(pattern, email)
                if match and match.group(0) == email:
                    return True
            return False
        return True

    def is_condition_satisfied(self, basket):
        """
        In addition to Oscar's check to see if the condition is satisfied,
        a check for if basket owners email domain is within the allowed email domains.
        """
        if not self.is_email_valid(basket.owner.email):
            return False
        return super(ConditionalOffer, self).is_condition_satisfied(basket)  # pylint: disable=bad-super-call


def validate_credit_seat_type(value):
    if len(value.split(',')) > 1 and 'credit' in value:
        raise ValidationError('Credit seat types cannot be paired with other seat types.')


class Range(AbstractRange):
    UPDATABLE_RANGE_FIELDS = [
        'catalog_query',
        'course_seat_types',
    ]
    catalog = models.ForeignKey('catalogue.Catalog', blank=True, null=True, related_name='ranges')
    catalog_query = models.TextField(blank=True, null=True)
    course_seat_types = models.CharField(
        max_length=255,
        validators=[validate_credit_seat_type],
        blank=True,
        null=True
    )

    def run_catalog_query(self, product):
        """
        Retrieve the results from running the query contained in catalog_query field.
        """
        cache_key = 'catalog_query_contains [{}] [{}]'.format(self.catalog_query, product.course_id)
        cache_hash = hashlib.md5(cache_key).hexdigest()
        response = cache.get(cache_hash)
        if not response:  # pragma: no cover
            request = get_current_request()
            try:
                response = request.site.siteconfiguration.course_catalog_api_client.course_runs.contains.get(
                    query=self.catalog_query,
                    course_run_ids=product.course_id,
                    partner=request.site.siteconfiguration.partner.short_code
                )
                cache.set(cache_hash, response, settings.COURSES_API_CACHE_TIMEOUT)
            except:  # pylint: disable=bare-except
                raise Exception('Could not contact Course Catalog Service.')

        return response

    def contains_product(self, product):
        """
        Assert if the range contains the product.
        """
        if self.catalog_query and self.course_seat_types:
            if product.attr.certificate_type.lower() in self.course_seat_types:  # pylint: disable=unsupported-membership-test
                response = self.run_catalog_query(product)
                # Range can have a catalog query and 'regular' products in it,
                # therefor an OR is used to check for both possibilities.
                return ((response['course_runs'][product.course_id]) or
                        super(Range, self).contains_product(product))  # pylint: disable=bad-super-call
        elif self.catalog:
            return (
                product.id in self.catalog.stock_records.values_list('product', flat=True) or
                super(Range, self).contains_product(product)  # pylint: disable=bad-super-call
            )
        return super(Range, self).contains_product(product)  # pylint: disable=bad-super-call

    contains = contains_product

    def num_products(self):
        return len(self.all_products())

    def all_products(self):
        if self.catalog_query and self.course_seat_types:
            # Backbone calls the Voucher Offers API endpoint which gets the products from the Course Catalog Service
            return []
        if self.catalog:
            catalog_products = [record.product for record in self.catalog.stock_records.all()]
            return catalog_products + list(super(Range, self).all_products())  # pylint: disable=bad-super-call
        return super(Range, self).all_products()  # pylint: disable=bad-super-call


from oscar.apps.offer.models import *  # noqa pylint: disable=wildcard-import,unused-wildcard-import,wrong-import-position,wrong-import-order,ungrouped-imports
