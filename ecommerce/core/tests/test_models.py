import ddt
import httpretty
import mock
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.test import override_settings
from edx_rest_api_client.auth import SuppliedJwtAuth
from requests.exceptions import ConnectionError

from ecommerce.core.models import BusinessClient, User, SiteConfiguration, validate_configuration
from ecommerce.core.tests import toggle_switch
from ecommerce.extensions.catalogue.tests.mixins import CourseCatalogTestMixin
from ecommerce.extensions.payment.tests.processors import DummyProcessor, AnotherDummyProcessor
from ecommerce.tests.factories import SiteConfigurationFactory
from ecommerce.tests.mixins import LmsApiMockMixin
from ecommerce.tests.testcases import TestCase

COURSE_CATALOG_API_URL = 'https://catalog.example.com/api/v1/'


def _make_site_config(payment_processors_str, site_id=1):
    site = Site.objects.get(id=site_id)

    return SiteConfiguration(
        site=site,
        payment_processors=payment_processors_str,
        from_email='sender@example.com'
    )


@ddt.ddt
class UserTests(CourseCatalogTestMixin, LmsApiMockMixin, TestCase):
    TEST_CONTEXT = {'foo': 'bar', 'baz': None}

    def test_access_token(self):
        user = self.create_user()
        self.assertIsNone(user.access_token)

        self.create_access_token(user)
        self.assertEqual(user.access_token, self.access_token)

    def test_tracking_context(self):
        """ Ensures that the tracking_context dictionary is written / read
        correctly by the User model. """
        user = self.create_user()
        self.assertIsNone(user.tracking_context)

        user.tracking_context = self.TEST_CONTEXT
        user.save()

        same_user = User.objects.get(id=user.id)
        self.assertEqual(same_user.tracking_context, self.TEST_CONTEXT)

    def test_get_full_name(self):
        """ Test that the user model concatenates first and last name if the full name is not set. """
        full_name = "George Costanza"
        user = self.create_user(full_name=full_name)
        self.assertEquals(user.get_full_name(), full_name)

        first_name = "Jerry"
        last_name = "Seinfeld"
        user = self.create_user(full_name=None, first_name=first_name, last_name=last_name)
        expected = "{first_name} {last_name}".format(first_name=first_name, last_name=last_name)
        self.assertEquals(user.get_full_name(), expected)

        user = self.create_user(full_name=full_name, first_name=first_name, last_name=last_name)
        self.assertEquals(user.get_full_name(), full_name)

    @httpretty.activate
    @ddt.data(('verified', False), ('professional', True), ('no-id-professional', False))
    @ddt.unpack
    def test_is_user_enrolled(self, mode, id_verification):
        """ Verify check for user enrollment in a course. """
        user = self.create_user()
        self.request.user = user
        course_id1 = 'course-v1:test+test+test'
        __, enrolled_seat = self.create_course_and_seat(
            course_id=course_id1, seat_type=mode, id_verification=id_verification
        )
        self.mock_enrollment_api(self.request, user, course_id1, mode=mode)
        self.assertTrue(user.is_user_already_enrolled(self.request, enrolled_seat))

        course_id2 = 'course-v1:not+enrolled+here'
        __, not_enrolled_seat = self.create_course_and_seat(
            course_id=course_id2, seat_type=mode, id_verification=id_verification
        )
        self.mock_enrollment_api(self.request, user, course_id2, is_active=False, mode=mode)
        self.assertFalse(user.is_user_already_enrolled(self.request, not_enrolled_seat))

    @httpretty.activate
    def test_user_details(self):
        """ Verify user details are returned. """
        user = self.create_user()
        user_details = {'is_active': True}
        self.mock_account_api(self.request, user.username, data=user_details)
        self.assertDictEqual(user.account_details(self.request), user_details)

    def test_no_user_details(self):
        """ Verify False is returned when there is a connection error. """
        user = self.create_user()
        with self.assertRaises(ConnectionError):
            self.assertFalse(user.account_details(self.request))

    def prepare_credit_eligibility_info(self, eligible=True):
        """ Helper method for setting up LMS eligibility info. """
        user = self.create_user()
        course_key = 'a/b/c'
        self.mock_eligibility_api(self.request, user, course_key, eligible=eligible)
        return user, course_key

    @httpretty.activate
    def test_user_is_eligible(self):
        """ Verify the method returns eligibility information. """
        user, course_key = self.prepare_credit_eligibility_info()
        self.assertEqual(user.is_eligible_for_credit(course_key)[0]['username'], user.username)
        self.assertEqual(user.is_eligible_for_credit(course_key)[0]['course_key'], course_key)

    @httpretty.activate
    def test_user_is_not_eligible(self):
        """ Verify method returns false (empty list) if user is not eligible. """
        user, course_key = self.prepare_credit_eligibility_info(eligible=False)
        self.assertFalse(user.is_eligible_for_credit(course_key))

    @httpretty.activate
    @ddt.data(True, False)
    def test_user_verification_status(self, is_verified):
        """ Verify the method returns correct response. """
        user = self.create_user()
        self.mock_verification_status_api(self.request, user, is_verified=is_verified)
        self.assertEqual(user.is_verified(), is_verified)


class BusinessClientTests(TestCase):
    def test_str(self):
        client = BusinessClient.objects.create(name='TestClient')
        self.assertEquals(str(client), 'TestClient')


@ddt.ddt
class SiteConfigurationTests(TestCase):
    @ddt.data(
        ("paypal", {"paypal"}),
        ("paypal ", {"paypal"}),
        ("paypal,cybersource", {"paypal", "cybersource"}),
        ("paypal, cybersource", {"paypal", "cybersource"}),
        ("paypal,cybersource,something_else", {"paypal", "cybersource", "something_else"}),
        ("paypal , cybersource , something_else", {"paypal", "cybersource", "something_else"}),
    )
    @ddt.unpack
    def test_payment_processor_field_parsing(self, payment_processors_str, expected_result):
        """
        Tests that comma-separated payment processor string is correctly converted to a set of payment processor names
        :param str payment_processors_str: comma-separated string of processor names (potentially with spaces)
        :param set[str] expected_result: expected payment_processors_set result
        """
        site_config = _make_site_config(payment_processors_str)
        self.assertEqual(site_config.payment_processors_set, expected_result)

    @ddt.data("paypal", "paypal, cybersource", "paypal , cybersource")
    def test_clean_fields_valid_values_pass_validation(self, payment_processors_str):
        """
        Tests that valid payment_processors value passes validation
        :param str payment_processors_str: comma-separated string of processor names (potentially with spaces)
        """
        site_config = _make_site_config(payment_processors_str)
        with mock.patch("ecommerce.extensions.payment.helpers.get_processor_class_by_name") as patched_proc_by_name:
            patched_proc_by_name.return_value = DummyProcessor
            try:
                site_config.clean_fields()
            except ValidationError as exc:
                self.fail(exc.message)

    @ddt.data(" ", "  \t ", "\t\n\r")
    def test_clean_fields_whitespace_payment_processor_fail_validation(self, payment_processors_str):
        """
        Tests that whitespace-only payment_processor values fail validation
        :param str payment_processors_str: comma-separated string of processor names (potentially with spaces)
        """
        site_config = _make_site_config(payment_processors_str)
        with self.assertRaises(ValidationError) as err:
            site_config.clean_fields()
            self.assertEqual(
                err.message, "Invalid payment processors field: must not only contain whitespace characters"
            )

    def test_clean_fields_unknown_payment_processor_fail_validation(self):
        """
        Tests that  validation fails if payment_processors field contains unknown payment processor names
        """
        site_config = _make_site_config("unknown_payment_processor")

        with self.assertRaises(ValidationError):
            site_config.clean_fields()

    def test_clean_fields_payment_processor_excluded_always_pass(self):
        """
        Tests that `clean_fields` pass if "payment_processors" are excluded, regardless of validity
        """
        site_config = _make_site_config("")
        site_config.clean_fields(exclude={"payment_processors"})

        site_config.payment_processors = "irrelevant-get_processor_by_name-is-patched"
        site_config.clean_fields(exclude={"payment_processors"})

    @ddt.data(None, '', ' ')
    def test_clean_client_side_payment_processor_with_empty_value(self, value):
        """ Verify validation succeeds if no value is set for the client_side_payment_processor field. """
        site_config = _make_site_config('paypal')
        site_config.client_side_payment_processor = value
        site_config.clean_fields()

    def test_clean_client_side_payment_processor_with_invalid_processor(self):
        """ Verify an error is raised if the value client_side_payment_processor is not in the list
        of available payment processors. """
        site_config = _make_site_config('paypal')
        site_config.client_side_payment_processor = 'bad-value'

        with self.assertRaises(ValidationError):
            site_config.clean_fields()

    def test_clean_client_side_payment_processor(self):
        """ Verify no error is raised if the value of client_side_payment_processor is in the
        list of available payment processors. """
        processor = 'paypal'
        site_config = _make_site_config(processor)
        site_config.client_side_payment_processor = processor
        site_config.clean_fields()

    @staticmethod
    def _enable_processor_switches(processors):
        for processor in processors:
            toggle_switch(settings.PAYMENT_PROCESSOR_SWITCH_PREFIX + processor.NAME, True)

    @override_settings(PAYMENT_PROCESSORS=[
        'ecommerce.extensions.payment.tests.processors.DummyProcessor',
        'ecommerce.extensions.payment.tests.processors.AnotherDummyProcessor',
    ])
    @ddt.data(
        ([], []),
        ([DummyProcessor], [DummyProcessor]),
        ([DummyProcessor, AnotherDummyProcessor], [DummyProcessor, AnotherDummyProcessor]),
    )
    @ddt.unpack
    def test_get_payment_processors(self, processors, expected_result):
        """ Tests that get_payment_processors returs correct payment processor classes """
        self._enable_processor_switches(processors)
        site_config = _make_site_config(",".join(proc.NAME for proc in processors))

        result = site_config.get_payment_processors()
        self.assertEqual(result, expected_result)

    @override_settings(PAYMENT_PROCESSORS=[
        'ecommerce.extensions.payment.tests.processors.DummyProcessor',
    ])
    def test_get_payment_processors_logs_warning_for_unknown_processors(self):
        """ Tests that get_payment_processors logs warnings if unknown payment processor codes are seen """
        processors = [DummyProcessor, AnotherDummyProcessor]
        site_config = _make_site_config(",".join(proc.NAME for proc in processors))
        with mock.patch("ecommerce.core.models.log") as patched_log:
            site_config.get_payment_processors()
            patched_log.warning.assert_called_once_with(
                'Unknown payment processors [%s] are configured for site %s',
                AnotherDummyProcessor.NAME,
                site_config.site.id
            )

    @override_settings(PAYMENT_PROCESSORS=[
        'ecommerce.extensions.payment.tests.processors.DummyProcessor',
        'ecommerce.extensions.payment.tests.processors.AnotherDummyProcessor',
    ])
    @ddt.data(
        [DummyProcessor],
        [DummyProcessor, AnotherDummyProcessor]
    )
    def test_get_payment_processors_switch_disabled(self, processors):
        """ Tests that get_payment_processors respects waffle switches """
        expected_result = []
        site_config = _make_site_config(",".join(proc.NAME for proc in processors))

        result = site_config.get_payment_processors()
        self.assertEqual(result, expected_result)

    def test_get_client_side_payment_processor(self):
        """ Verify the method returns the client-side payment processor. """
        PROCESSOR_NAME = 'cybersource'
        site_config = _make_site_config(PROCESSOR_NAME)

        site_config.client_side_payment_processor = None
        self.assertIsNone(site_config.get_client_side_payment_processor_class())

        site_config.client_side_payment_processor = PROCESSOR_NAME
        self.assertEqual(site_config.get_client_side_payment_processor_class().NAME, PROCESSOR_NAME)

    def test_get_from_email(self):
        """
        Validate SiteConfiguration.get_from_email() along with whether, or not,
        the base from email address is actually changed when a site-specific value is specified.
        """
        site_config = SiteConfigurationFactory(from_email='', partner__name='TestX')
        self.assertEqual(site_config.get_from_email(), settings.OSCAR_FROM_EMAIL)

        expected_from_email = "expected@email.com"
        site_config = SiteConfigurationFactory(from_email=expected_from_email, partner__name='TestX')
        self.assertEqual(site_config.get_from_email(), expected_from_email)

    @httpretty.activate
    def test_access_token(self):
        """ Verify the property retrieves, and caches, an access token from the OAuth 2.0 provider. """
        token = self.mock_access_token_response()
        self.assertEqual(self.site.siteconfiguration.access_token, token)
        self.assertTrue(httpretty.has_request())

        # Verify the value is cached
        httpretty.disable()
        self.assertEqual(self.site.siteconfiguration.access_token, token)

    @httpretty.activate
    @override_settings(COURSE_CATALOG_API_URL=COURSE_CATALOG_API_URL)
    def test_course_catalog_api_client(self):
        """ Verify the property returns a Course Catalog API client. """
        token = self.mock_access_token_response()
        client = self.site.siteconfiguration.course_catalog_api_client
        client_store = client._store  # pylint: disable=protected-access
        client_auth = client_store['session'].auth

        self.assertEqual(client_store['base_url'], COURSE_CATALOG_API_URL)
        self.assertIsInstance(client_auth, SuppliedJwtAuth)
        self.assertEqual(client_auth.token, token)


class HelperMethodTests(TestCase):
    """ Tests helper methods in models.py """

    def setUp(self):
        """ setUp test """
        self.site_config_objects = mock.Mock()

        patcher = mock.patch('ecommerce.core.models.SiteConfiguration.objects', self.site_config_objects)
        patcher.start()

        self.addCleanup(patcher.stop)

    @override_settings(PAYMENT_PROCESSORS=[
        'ecommerce.extensions.payment.tests.processors.DummyProcessor',
        'ecommerce.extensions.payment.tests.processors.AnotherDummyProcessor',
    ])
    def test_validate_configuration_passes(self):
        """
        Test that site configurations with available payment processor(s) pass validation
        """
        config1 = _make_site_config(DummyProcessor.NAME)
        config2 = _make_site_config(DummyProcessor.NAME + ',' + AnotherDummyProcessor.NAME)

        self.site_config_objects.all.return_value = [config1, config2]

        validate_configuration()  # checks that no exception is thrown

    @override_settings(PAYMENT_PROCESSORS=[
        'ecommerce.extensions.payment.tests.processors.DummyProcessor',
    ])
    def test_validate_configuration_fails(self):
        """
        Test that site configurations with unknown payment processor(s) fail validation
        """
        config1 = _make_site_config(DummyProcessor.NAME)
        config2 = _make_site_config(DummyProcessor.NAME + ',' + AnotherDummyProcessor.NAME)

        self.site_config_objects.all.return_value = [config1, config2]

        with self.assertRaises(ValidationError):
            validate_configuration()
