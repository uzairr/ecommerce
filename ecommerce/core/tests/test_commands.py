from __future__ import unicode_literals

from ddt import ddt, data
from django.contrib.sites.models import Site
from django.core.management import call_command, CommandError
from oscar.core.loading import get_model

from ecommerce.tests.testcases import TestCase

Partner = get_model('partner', 'Partner')


@ddt
class CreateOrUpdateSiteCommandTests(TestCase):

    command_name = 'create_or_update_site'

    def setUp(self):
        super(CreateOrUpdateSiteCommandTests, self).setUp()

        self.partner = 'fake'
        self.lms_url_root = 'http://fake.server'
        self.theme_scss_path = 'sass/themes/edx.scss'
        self.payment_processors = 'cybersource,paypal'
        self.client_id = 'ecommerce-key'
        self.client_secret = 'ecommerce-secret'
        self.segment_key = 'test-segment-key'
        self.from_email = 'site_from_email@example.com'
        self.payment_support_email = 'support@example.com'
        self.payment_support_url = 'http://fake.server/support'

    def _check_site_configuration(self, site, partner):
        site_configuration = site.siteconfiguration
        self.assertEqual(site_configuration.site, site)
        self.assertEqual(site_configuration.partner, partner)
        self.assertEqual(site_configuration.lms_url_root, self.lms_url_root)
        self.assertEqual(site_configuration.theme_scss_path, self.theme_scss_path)
        self.assertEqual(site_configuration.payment_processors, self.payment_processors)
        self.assertEqual(site_configuration.oauth_settings['SOCIAL_AUTH_EDX_OIDC_KEY'], self.client_id)
        self.assertEqual(site_configuration.oauth_settings['SOCIAL_AUTH_EDX_OIDC_SECRET'], self.client_secret)
        self.assertEqual(site_configuration.segment_key, self.segment_key)
        self.assertEqual(site_configuration.from_email, self.from_email)

    def _call_command(self, site_domain, partner_code, lms_url_root, client_id, client_secret, from_email,
                      site_id=None, site_name=None, partner_name=None, theme_scss_path=None,
                      payment_processors=None, segment_key=None, enable_enrollment_codes=False,
                      payment_support_email=None, payment_support_url=None):
        """
        Internal helper method for interacting with the create_or_update_site management command
        """
        # Required arguments
        command_args = [
            '--site-domain={site_domain}'.format(site_domain=site_domain),
            '--partner-code={partner_code}'.format(partner_code=partner_code),
            '--lms-url-root={lms_url_root}'.format(lms_url_root=lms_url_root),
            '--client-id={client_id}'.format(client_id=client_id),
            '--client-secret={client_secret}'.format(client_secret=client_secret),
            '--from-email={from_email}'.format(from_email=from_email)
        ]

        # Optional arguments
        if site_id:
            command_args.append('--site-id={site_id}'.format(site_id=site_id))
        if site_name:
            command_args.append('--site-name={site_name}'.format(site_name=site_name))
        if partner_name:
            command_args.append('--partner-name={partner_name}'.format(partner_name=partner_name))
        if theme_scss_path:
            command_args.append('--theme-scss-path={theme_scss_path}'.format(theme_scss_path=theme_scss_path))
        if payment_processors:
            command_args.append('--payment-processors={payment_processors}'.format(
                payment_processors=payment_processors
            ))
        if segment_key:
            command_args.append('--segment-key={segment_key}'.format(segment_key=segment_key))
        if enable_enrollment_codes:
            command_args.append('--enable-enrollment-codes={enable_enrollment_codes}'.format(
                enable_enrollment_codes=enable_enrollment_codes
            ))
        if payment_support_email:
            command_args.append('--payment-support-email={payment_support_email}'.format(
                payment_support_email=payment_support_email
            ))
        if payment_support_url:
            command_args.append('--payment-support-url={payment_support_url}'.format(
                payment_support_url=payment_support_url
            ))
        call_command(self.command_name, *command_args)

    def test_create_site(self):
        """ Verify the command creates Site, Partner, and SiteConfiguration. """
        site_domain = 'ecommerce-fake1.server'

        self._call_command(
            site_domain=site_domain,
            partner_code=self.partner,
            lms_url_root=self.lms_url_root,
            theme_scss_path=self.theme_scss_path,
            payment_processors=self.payment_processors,
            client_id=self.client_id,
            client_secret=self.client_secret,
            segment_key=self.segment_key,
            from_email=self.from_email
        )

        site = Site.objects.get(domain=site_domain)
        partner = Partner.objects.get(code=self.partner)

        self._check_site_configuration(site, partner)
        self.assertFalse(site.siteconfiguration.enable_enrollment_codes)

    def test_update_site(self):
        """ Verify the command updates Site and creates Partner, and SiteConfiguration """
        site_domain = 'ecommerce-fake2.server'
        updated_site_domain = 'ecommerce-fake3.server'
        updated_site_name = 'Fake Ecommerce Server'
        site = Site.objects.create(domain=site_domain)

        self._call_command(
            site_id=site.id,
            site_domain=updated_site_domain,
            site_name=updated_site_name,
            partner_code=self.partner,
            lms_url_root=self.lms_url_root,
            theme_scss_path=self.theme_scss_path,
            payment_processors=self.payment_processors,
            client_id=self.client_id,
            client_secret=self.client_secret,
            segment_key=self.segment_key,
            from_email=self.from_email,
            enable_enrollment_codes=True,
            payment_support_email=self.payment_support_email,
            payment_support_url=self.payment_support_url
        )

        site = Site.objects.get(id=site.id)
        partner = Partner.objects.get(code=self.partner)

        self.assertEqual(site.domain, updated_site_domain)
        self.assertEqual(site.name, updated_site_name)
        self._check_site_configuration(site, partner)
        self.assertTrue(site.siteconfiguration.enable_enrollment_codes)
        self.assertEqual(site.siteconfiguration.payment_support_email, self.payment_support_email)
        self.assertEqual(site.siteconfiguration.payment_support_url, self.payment_support_url)

    @data(
        ['--site-id=1'],
        ['--site-id=1', '--site-name=fake.server'],
        ['--site-id=1', '--site-name=fake.server', '--partner-name=fake_partner'],
        ['--site-id=1', '--site-domain=fake.server', '--partner-name=fake_partner',
         '--theme-scss-path=site/sass/css/'],
        ['--site-id=1', '--site-domain=fake.server', '--partner-name=fake_partner',
         '--theme-scss-path=site/sass/css/', '--payment-processors=cybersource'],
        ['--site-id=1', '--site-domain=fake.server', '--partner-name=fake_partner',
         '--theme-scss-path=site/sass/css/', '--payment-processors=cybersource',
         '--segment-key=abc']
    )
    def test_missing_arguments(self, command_args):
        """ Verify CommandError is raised when required arguments are missing """
        with self.assertRaises(CommandError):
            call_command(self.command_name, *command_args)
