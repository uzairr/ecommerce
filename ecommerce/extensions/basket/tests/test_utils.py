import datetime
import json
import mock

import ddt
import httpretty
from django.test import RequestFactory
from oscar.core.loading import get_model
from oscar.test.factories import BasketFactory, ProductFactory, RangeFactory, VoucherFactory
import pytz

from ecommerce.core.constants import ENROLLMENT_CODE_PRODUCT_CLASS_NAME, ENROLLMENT_CODE_SWITCH
from ecommerce.core.tests import toggle_switch
from ecommerce.courses.tests.factories import CourseFactory
from ecommerce.extensions.basket.utils import check_sdn, prepare_basket, attribute_cookie_data
from ecommerce.extensions.catalogue.tests.mixins import CourseCatalogTestMixin
from ecommerce.extensions.partner.models import StockRecord
from ecommerce.extensions.payment.models import SDNCheckFailures
from ecommerce.extensions.test.factories import prepare_voucher
from ecommerce.referrals.models import Referral
from ecommerce.tests.factories import SiteConfigurationFactory
from ecommerce.tests.testcases import TestCase

Benefit = get_model('offer', 'Benefit')
Basket = get_model('basket', 'Basket')
Product = get_model('catalogue', 'Product')


@ddt.ddt
class BasketUtilsTests(CourseCatalogTestMixin, TestCase):
    """ Tests for basket utility functions. """

    def setUp(self):
        super(BasketUtilsTests, self).setUp()
        self.request = RequestFactory()
        self.request.COOKIES = {}
        self.request.user = self.create_user()
        site_configuration = SiteConfigurationFactory(partner__name='Tester')
        site_configuration.utm_cookie_name = 'test.edx.utm'
        self.request.site = site_configuration.site

    def test_prepare_basket_with_voucher(self):
        """ Verify a basket is returned and contains a voucher and the voucher is applied. """
        # Prepare a product with price of 100 and a voucher with 10% discount for that product.
        product = ProductFactory(stockrecords__price_excl_tax=100)
        new_range = RangeFactory(products=[product, ])
        voucher, product = prepare_voucher(_range=new_range, benefit_value=10)

        stock_record = StockRecord.objects.get(product=product)
        self.assertEqual(stock_record.price_excl_tax, 100.00)

        basket = prepare_basket(self.request, product, voucher)
        self.assertIsNotNone(basket)
        self.assertEqual(basket.status, Basket.OPEN)
        self.assertEqual(basket.lines.count(), 1)
        self.assertEqual(basket.lines.first().product, product)
        self.assertEqual(basket.vouchers.count(), 1)
        self.assertIsNotNone(basket.applied_offers())
        self.assertEqual(basket.total_discount, 10.00)
        self.assertEqual(basket.total_excl_tax, 90.00)

    def test_prepare_basket_enrollment_with_voucher(self):
        """Verify the basket does not contain a voucher if enrollment code is added to it."""
        course = CourseFactory()
        toggle_switch(ENROLLMENT_CODE_SWITCH, True)
        course.create_or_update_seat('verified', False, 10, self.partner, create_enrollment_code=True)
        enrollment_code = Product.objects.get(product_class__name=ENROLLMENT_CODE_PRODUCT_CLASS_NAME)
        voucher, product = prepare_voucher()

        basket = prepare_basket(self.request, product, voucher)
        self.assertIsNotNone(basket)
        self.assertEqual(basket.all_lines()[0].product, product)
        self.assertTrue(basket.contains_a_voucher)

        basket = prepare_basket(self.request, enrollment_code, voucher)
        self.assertIsNotNone(basket)
        self.assertEqual(basket.all_lines()[0].product, enrollment_code)
        self.assertFalse(basket.contains_a_voucher)

    def test_multiple_vouchers(self):
        """ Verify only the last entered voucher is contained in the basket. """
        product = ProductFactory()
        voucher1 = VoucherFactory(code='FIRST')
        basket = prepare_basket(self.request, product, voucher1)
        self.assertEqual(basket.vouchers.count(), 1)
        self.assertEqual(basket.vouchers.first(), voucher1)

        voucher2 = VoucherFactory(code='SECOND')
        new_basket = prepare_basket(self.request, product, voucher2)
        self.assertEqual(basket, new_basket)
        self.assertEqual(new_basket.vouchers.count(), 1)
        self.assertEqual(new_basket.vouchers.first(), voucher2)

    def test_prepare_basket_without_voucher(self):
        """ Verify a basket is returned and does not contain a voucher. """
        product = ProductFactory()
        basket = prepare_basket(self.request, product)
        self.assertIsNotNone(basket)
        self.assertEqual(basket.status, Basket.OPEN)
        self.assertEqual(basket.lines.count(), 1)
        self.assertEqual(basket.lines.first().product, product)
        self.assertFalse(basket.vouchers.all())
        self.assertFalse(basket.applied_offers())

    def test_prepare_basket_with_multiple_products(self):
        """ Verify a basket is returned and only contains a single product. """
        product1 = ProductFactory(stockrecords__partner__short_code='test1')
        product2 = ProductFactory(stockrecords__partner__short_code='test2')
        basket = prepare_basket(self.request, product1)
        basket = prepare_basket(self.request, product2)
        self.assertIsNotNone(basket)
        self.assertEqual(basket.status, Basket.OPEN)
        self.assertEqual(basket.lines.count(), 1)
        self.assertEqual(basket.lines.first().product, product2)
        self.assertEqual(basket.product_quantity(product2), 1)

    def test_prepare_basket_calls_attribution_method(self):
        """ Verify a basket is returned and referral method called. """
        with mock.patch('ecommerce.extensions.basket.utils.attribute_cookie_data') as mock_attr_method:
            product = ProductFactory()
            basket = prepare_basket(self.request, product)
            mock_attr_method.assert_called_with(basket, self.request)

    def test_attribute_cookie_data_affiliate_cookie_lifecycle(self):
        """ Verify a basket is returned and referral captured. """
        affiliate_id = 'test_affiliate'
        self.request.COOKIES['affiliate_id'] = affiliate_id
        basket = BasketFactory(owner=self.request.user, site=self.request.site)
        attribute_cookie_data(basket, self.request)

        # test affiliate id from cookie saved in referral
        referral = Referral.objects.get(basket_id=basket.id)
        self.assertEqual(referral.affiliate_id, affiliate_id)

        # update cookie
        new_affiliate_id = 'new_affiliate'
        self.request.COOKIES['affiliate_id'] = new_affiliate_id
        attribute_cookie_data(basket, self.request)

        # test new affiliate id saved
        referral = Referral.objects.get(basket_id=basket.id)
        self.assertEqual(referral.affiliate_id, new_affiliate_id)

        # expire cookie
        del self.request.COOKIES['affiliate_id']
        attribute_cookie_data(basket, self.request)

        # test referral record is deleted when no cookie set
        with self.assertRaises(Referral.DoesNotExist):
            Referral.objects.get(basket_id=basket.id)

    def test_attribute_cookie_data_utm_cookie_lifecycle(self):
        """ Verify a basket is returned and referral captured. """
        utm_source = 'test-source'
        utm_medium = 'test-medium'
        utm_campaign = 'test-campaign'
        utm_term = 'test-term'
        utm_content = 'test-content'
        utm_created_at = 1475590280823
        expected_created_at = datetime.datetime.fromtimestamp(int(utm_created_at) / float(1000), tz=pytz.UTC)

        utm_cookie = {
            'utm_source': utm_source,
            'utm_medium': utm_medium,
            'utm_campaign': utm_campaign,
            'utm_term': utm_term,
            'utm_content': utm_content,
            'created_at': utm_created_at,
        }

        self.request.COOKIES['test.edx.utm'] = json.dumps(utm_cookie)
        basket = BasketFactory(owner=self.request.user, site=self.request.site)
        attribute_cookie_data(basket, self.request)

        # test utm data from cookie saved in referral
        referral = Referral.objects.get(basket_id=basket.id)
        self.assertEqual(referral.utm_source, utm_source)
        self.assertEqual(referral.utm_medium, utm_medium)
        self.assertEqual(referral.utm_campaign, utm_campaign)
        self.assertEqual(referral.utm_term, utm_term)
        self.assertEqual(referral.utm_content, utm_content)
        self.assertEqual(referral.utm_created_at, expected_created_at)

        # update cookie
        utm_source = 'test-source-new'
        utm_medium = 'test-medium-new'
        utm_campaign = 'test-campaign-new'
        utm_term = 'test-term-new'
        utm_content = 'test-content-new'
        utm_created_at = 1470590000000
        expected_created_at = datetime.datetime.fromtimestamp(int(utm_created_at) / float(1000), tz=pytz.UTC)

        new_utm_cookie = {
            'utm_source': utm_source,
            'utm_medium': utm_medium,
            'utm_campaign': utm_campaign,
            'utm_term': utm_term,
            'utm_content': utm_content,
            'created_at': utm_created_at,
        }
        self.request.COOKIES['test.edx.utm'] = json.dumps(new_utm_cookie)
        attribute_cookie_data(basket, self.request)

        # test new utm data saved
        referral = Referral.objects.get(basket_id=basket.id)
        self.assertEqual(referral.utm_source, utm_source)
        self.assertEqual(referral.utm_medium, utm_medium)
        self.assertEqual(referral.utm_campaign, utm_campaign)
        self.assertEqual(referral.utm_term, utm_term)
        self.assertEqual(referral.utm_content, utm_content)
        self.assertEqual(referral.utm_created_at, expected_created_at)

        # expire cookie
        del self.request.COOKIES['test.edx.utm']
        attribute_cookie_data(basket, self.request)

        # test referral record is deleted when no cookie set
        with self.assertRaises(Referral.DoesNotExist):
            Referral.objects.get(basket_id=basket.id)

    def test_attribute_cookie_data_multiple_cookies(self):
        """ Verify a basket is returned and referral captured. """
        utm_source = 'test-source'
        utm_medium = 'test-medium'
        utm_campaign = 'test-campaign'
        utm_term = 'test-term'
        utm_content = 'test-content'
        utm_created_at = 1475590280823

        utm_cookie = {
            'utm_source': utm_source,
            'utm_medium': utm_medium,
            'utm_campaign': utm_campaign,
            'utm_term': utm_term,
            'utm_content': utm_content,
            'created_at': utm_created_at,
        }

        affiliate_id = 'affiliate'

        self.request.COOKIES['test.edx.utm'] = json.dumps(utm_cookie)
        self.request.COOKIES['affiliate_id'] = affiliate_id
        basket = BasketFactory(owner=self.request.user, site=self.request.site)
        attribute_cookie_data(basket, self.request)

        # test affiliate id & UTM data from cookie saved in referral
        referral = Referral.objects.get(basket_id=basket.id)
        expected_created_at = datetime.datetime.fromtimestamp(int(utm_created_at) / float(1000), tz=pytz.UTC)
        self.assertEqual(referral.utm_source, utm_source)
        self.assertEqual(referral.utm_medium, utm_medium)
        self.assertEqual(referral.utm_campaign, utm_campaign)
        self.assertEqual(referral.utm_term, utm_term)
        self.assertEqual(referral.utm_content, utm_content)
        self.assertEqual(referral.utm_created_at, expected_created_at)
        self.assertEqual(referral.affiliate_id, affiliate_id)

        # expire 1 cookie
        del self.request.COOKIES['test.edx.utm']
        attribute_cookie_data(basket, self.request)

        # test affiliate id still saved in referral but utm data removed
        referral = Referral.objects.get(basket_id=basket.id)
        self.assertEqual(referral.utm_source, '')
        self.assertEqual(referral.utm_medium, '')
        self.assertEqual(referral.utm_campaign, '')
        self.assertEqual(referral.utm_term, '')
        self.assertEqual(referral.utm_content, '')
        self.assertIsNone(referral.utm_created_at)
        self.assertEqual(referral.affiliate_id, affiliate_id)

        # expire other cookie
        del self.request.COOKIES['affiliate_id']
        attribute_cookie_data(basket, self.request)

        # test referral record is deleted when no cookies are set
        with self.assertRaises(Referral.DoesNotExist):
            Referral.objects.get(basket_id=basket.id)

    def prepare_sdn_check_values(self):
        """ Enable SDN check and values to the site configuration. """
        self.request.user.full_name = 'SDN tester'
        config = self.request.site.siteconfiguration
        config.enable_sdn_check = True
        config.sdn_api_url = 'http://sdn-test.fake'
        config.sdn_api_key = 'fake-key'
        config.sdn_api_list = 'SDN,TEST'
        config.save()

    def mock_sdn_response(self, response, status_code=200):
        """ Mock the SDN check API endpoint response. """
        self.prepare_sdn_check_values()
        config = self.request.site.siteconfiguration
        sdn_query_url = '{sdn_api}/?sources={sdn_list}&api_key={sdn_key}&type=individual&q={full_name}'.format(
            sdn_api=config.sdn_api_url,
            sdn_list=config.sdn_api_list,
            sdn_key=config.sdn_api_key,
            full_name=self.request.user.full_name
        )
        httpretty.register_uri(
            httpretty.GET,
            sdn_query_url,
            status=status_code,
            body=json.dumps(response),
            content_type='application/json'
        )

    def assert_sdn_failure(self, basket, failure_type, response):
        """ Assert an SDN failure is logged and has the correct values. """
        self.assertEqual(SDNCheckFailures.objects.count(), 1)
        sdn_object = SDNCheckFailures.objects.first()
        self.assertEqual(sdn_object.full_name, self.request.user.full_name)
        self.assertEqual(sdn_object.sdn_check_response, response)
        self.assertEqual(sdn_object.failure_type, failure_type)
        self.assertEqual(sdn_object.basket, basket)

    @httpretty.activate
    def test_sdn_check_connection_error(self):
        """ Verify an SDN failure is logged in case of a connection error. """
        sdn_response = {}
        self.mock_sdn_response(sdn_response, status_code=400)
        basket = BasketFactory(owner=self.request.user, site=self.request.site)
        self.assertEqual(SDNCheckFailures.objects.count(), 0)
        self.assertTrue(check_sdn(self.request))

        self.assert_sdn_failure(basket, SDNCheckFailures.CONN_ERR, '')

    @httpretty.activate
    def test_sdn_check_match(self):
        """ Verify the SDN check returns false for a match and records it. """
        sdn_response = {'total': 1}
        self.mock_sdn_response(sdn_response)
        basket = BasketFactory(owner=self.request.user, site=self.request.site)
        self.assertEqual(SDNCheckFailures.objects.count(), 0)
        self.assertFalse(check_sdn(self.request))

        self.assert_sdn_failure(basket, SDNCheckFailures.MATCHED, json.dumps(sdn_response))

    @httpretty.activate
    def test_sdn_check_pass(self):
        """ Verify the SDN check returns true if the user passed. """
        self.mock_sdn_response({'total': 0})
        BasketFactory(owner=self.request.user, site=self.request.site)
        self.assertEqual(SDNCheckFailures.objects.count(), 0)
        self.assertTrue(check_sdn(self.request))
        self.assertEqual(SDNCheckFailures.objects.count(), 0)
