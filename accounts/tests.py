import factory

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.contrib.auth.views import login
from django.contrib.sites.shortcuts import get_current_site
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.urlresolvers import resolve, reverse
from django.test import Client, TestCase, RequestFactory

from connect_config.factories import SiteConfigFactory

from .factories import (BrandFactory, InvitedPendingFactory, ModeratorFactory,
                        RequestedPendingFactory, UserFactory, UserLinkFactory,
                        UserSkillFactory)

from .forms import validate_email_availability, RequestInvitationForm
from .models import CustomUser, UserLink, UserSkill
from .utils import create_inactive_user, invite_user_to_reactivate_account
from .views import (account_settings, activate_account, close_account,
                    profile_settings, request_invitation, update_account)


User = get_user_model()


# Models.py

class UserModelTest(TestCase):
    fixtures = ['group_perms']

    def setUp(self):
        self.moderator = ModeratorFactory()
        self.standard_user = UserFactory()
        self.invited_pending = InvitedPendingFactory()
        self.requested_pending = RequestedPendingFactory()

    def test_moderator_can_invite_new_user(self):
        user = self.moderator.invite_new_user(email='standard_user@test.test',
                                              first_name='standard_user',
                                              last_name='user')

        self.assertEqual(user.email,'standard_user@test.test')
        self.assertEqual(user.first_name, 'standard_user')
        self.assertEqual(user.last_name, 'user')
        self.assertEqual(user.registration_method, CustomUser.INVITED)
        self.assertEqual(user.moderator, self.moderator)
        self.assertEqual(user.moderator_decision, CustomUser.PRE_APPROVED)
        self.assertIsNotNone(user.decision_datetime)
        self.assertIsNotNone(user.auth_token)

    def test_standard_user_user_cannot_invite_new_user(self):
        with self.assertRaises(PermissionDenied):
            user = self.standard_user.invite_new_user(email='standard_user@test.test',
                                                 first_name='standard_user',
                                                 last_name='user')
            self.assertIsNone(user)

    def test_moderator_can_reinvite_user(self):

        decision_datetime = self.invited_pending.decision_datetime
        auth_token = self.invited_pending.auth_token

        self.moderator.reinvite_user(user=self.invited_pending,
                                     email='reset_email@test.test')

        self.assertEqual(self.invited_pending.email, 'reset_email@test.test')
        self.assertNotEqual(self.invited_pending.decision_datetime, decision_datetime)
        self.assertNotEqual(self.invited_pending.auth_token, auth_token)

    def test_standard_user_user_cannot_reinvite_user(self):

        decision_datetime = self.invited_pending.decision_datetime
        auth_token = self.invited_pending.auth_token

        with self.assertRaises(PermissionDenied):
            self.standard_user.reinvite_user(user=self.invited_pending,
                                        email='reset_email@test.test')

            self.assertNotEqual(self.invited_pending.email, 'reset_email@test.test')
            self.assertEqual(self.invited_pending.decision_datetime, decision_datetime)
            self.assertEqual(self.invited_pending.auth_token, auth_token)

    def test_moderator_can_approve_user_application(self):
        self.moderator.approve_user_application(self.requested_pending)

        self.assertEqual(self.requested_pending.moderator, self.moderator)
        self.assertEqual(self.requested_pending.moderator_decision, CustomUser.APPROVED)
        self.assertIsNotNone(self.requested_pending.decision_datetime)
        self.assertIsNotNone(self.requested_pending.auth_token)

    def test_standard_user_user_cannot_approve_user_application(self):
        with self.assertRaises(PermissionDenied):
            self.standard_user.approve_user_application(self.requested_pending)

            self.assertIsNone(self.requested_pending.moderator)
            self.assertFalse(self.requested_pending.moderator_decision)
            self.assertIsNone(self.requested_pending.decision_datetime)
            self.assertFalse(self.requested_pending.auth_token)

    def test_moderator_can_reject_user_application(self):
        self.moderator.reject_user_application(self.requested_pending)

        self.assertEqual(self.requested_pending.moderator, self.moderator)
        self.assertEqual(self.requested_pending.moderator_decision, CustomUser.REJECTED)
        self.assertIsNotNone(self.requested_pending.decision_datetime)
        self.assertIsNotNone(self.requested_pending.auth_token)

    def test_standard_user_user_cannot_reject_user_application(self):
        with self.assertRaises(PermissionDenied):
            self.standard_user.reject_user_application(self.requested_pending)

            self.assertIsNone(self.requested_pending.moderator)
            self.assertFalse(self.requested_pending.moderator_decision)
            self.assertIsNone(self.requested_pending.decision_datetime)
            self.assertFalse(self.requested_pending.auth_token)


class UserSkillTest(TestCase):
    def test_proficiency_percentage_calculates_correctly(self):
        user_skill = UserSkillFactory(proficiency=UserSkill.INTERMEDIATE)
        percentage = user_skill.get_proficiency_percentage()

        self.assertEquals(percentage, 50)


class UserLinkTest(TestCase):
    def setUp(self):
        self.github = BrandFactory() # Github is default brand.

    def test_custom_save_method_finds_registered_brand(self):
        user_link = UserLinkFactory(url='http://github.com/nlh-kabu')

        self.assertEqual(user_link.icon, self.github)

    def test_custom_save_method_cannot_find_unregistered_brand(self):
        user_link = UserLinkFactory(url='http://blahblah.com/nlh-kabu')

        self.assertIsNone(user_link.icon)

    def test_get_icon_method_gets_correct_icon(self):
        user_link = UserLinkFactory(url='http://github.com/nlh-kabu')
        icon = user_link.get_icon()

        self.assertEqual(icon, 'fa-github')

    def test_get_icon_method_gets_default_icon(self):
        user_link = UserLinkFactory(url='http://noiconurl.com')
        icon = user_link.get_icon()

        self.assertEqual(icon, 'fa-globe')


class LinkBrandTest(TestCase):
    def test_custom_save_method_applies_new_brand_to_existing_userlinks(self):
        UserLinkFactory(url='http://facebook.com/myusername')

        new_brand = BrandFactory(name='Facebook',
                                 domain='facebook.com',
                                 fa_icon='fa-facebook')

        # Retreive the link to check that it has the new brand
        link = UserLink.objects.get(url='http://facebook.com/myusername')

        self.assertEqual(link.icon, new_brand)


# Forms.py

class FormValidationTest(TestCase):

    def setUp(self):
        existing_user = UserFactory(email='existing.user@test.test')

    def test_email_is_unique(self):
        unique = validate_email_availability('unique_user@test.test')
        self.assertTrue(unique)

    def test_email_is_duplicate(self):
        with self.assertRaises(ValidationError):
            validate_email_availability('existing.user@test.test')


class RequestInvitationFormTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        site = get_current_site(self.client.request)
        site.config = SiteConfigFactory(site=site)

    def test_closed_account_prompts_custom_validation_message(self):
        closed_user = UserFactory(
            email='closed.user@test.test',
            is_closed=True,
        )

        request = self.factory.get(reverse('accounts:request-invitation'))

        form = RequestInvitationForm(
            request = request,
            data = {
                'first_name': 'First',
                'last_name': 'Last',
                'email': 'closed.user@test.test',
                'comments': 'I would like an account',
            }
        )

        self.assertFalse(form.is_valid())
        # TODO: Check that correct error is raised - code='email_registered_to_closed_account'
        # TODO: Check that invite_user_to_reactivate_account() is called.




#~class ActivateAccountFormTest(TestCase):
    #~def test_password_validation_fails_when_passwords_are_different(self):
    #~def test_password_validation_passes_when_passwords_are_same(self):


#~class ProfileFormTest(TestCase):
    #~def test_profile_form_is_prepopulated_with_users_data(self):

#~class SkillFormsetTest(TestCase):
    #~def test_skill_formset_is_prepopulated_with_users_skills(self):
    #~def test_validation_fails_when_userskill_is_not_unique_to_user(self):
    #~def test_validation_passes_when_userskill_is_unique_to_user(self):
    #~def test_validation_fails_when_userskill_has_skill_but_no_proficiency(self):
    #~def test_validation_fails_when_userskill_has_proficicency_but_no_skill(self):
    #~def test_validation_passes_when_userskill_has_skill_and_proficiency(self):
    #~def test_validation_passes_when_both_skill_and_proficiency_are_empty(self):
#~
#~class LinkFormsetTest(TestCase):
    #~def test_link_formset_is_prepopulated_with_users_links(self):
    #~def test_validation_fails_when_link_url_is_not_unique_to_user(self):
    #~def test_validation_passes_when_link_url_is_unique_to_user(self):
    #~def test_validation_fails_when_link_anchor_is_not_unique_to_user(self):
    #~def test_validation_passes_when_link_anchor_is_unique_to_user(self):
    #~def test_validation_fails_when_link_has_anchor_but_no_url(self):
    #~def test_validation_fails_when_link_has_url_but_no_anchor(self):
    #~def test_validation_passes_when_link_has_url_and_anchor(self):
    #~def test_validation_passes_when_both_url_and_anchor_are_empty(self):
#~
#~class AccountSettingsFormTest(TestCase):
    #~def test_current_password_matches_users_password(self):
    #~def test_validation_fails_if_user_tries_to_change_password_without_current_password(self):
    #~def test_validation_fails_if_user_tries_to_change_password_without_confirming_password(self):
    #~def test_password_validation_fails_when_passwords_are_different(self):
    #~def test_password_validation_passes_when_passwords_are_same(self):
#~

#~class CloseAccountFormTest(TestCase):
    #~def test_email_field_is_prepopulated_with_user_email(self):
    #~def test_current_password_matches_users_password(self):


# Utils.py

class AccountUtilsTest(TestCase):
    fixtures = ['group_perms']

    def setUp(self):
        self.standard_user = UserFactory()
        self.factory = RequestFactory()
        site = get_current_site(self.client.request)
        site.config = SiteConfigFactory(site=site)

    def test_create_inactive_user(self):
        user = create_inactive_user('test@test.test', 'first', 'last')
        moderators = Group.objects.get(name='moderators')

        self.assertEqual(user.email, 'test@test.test')
        self.assertEqual(user.first_name, 'first')
        self.assertEqual(user.last_name, 'last')
        self.assertEqual(user.is_active, False)
        self.assertEqual(user.is_moderator, False)
        self.assertNotIn(moderators, user.groups.all())

    def test_reactivated_account_token_is_reset(self):
        initial_token = self.standard_user.auth_token
        request = self.factory.get(reverse('accounts:request-invitation'))
        user = invite_user_to_reactivate_account(self.standard_user, request)

        self.assertNotEqual(initial_token, user.auth_token)
        self.assertFalse(user.auth_token_is_used)

    #~def test_reactivation_email_sent_to_user():


# Urls.py and views.py

class RequestInvitationTest(TestCase):

    def setUp(self):
        self.client = Client()

    def test_request_invitation_url_resolves_to_request_invitation_view(self):
        url = resolve('/accounts/request-invitation')

        self.assertEqual(url.func, request_invitation)

    def test_requested_account_registration_recorded(self):
        response = self.client.post(
            reverse('accounts:request-invitation'),
            data = {
                'first_name': 'First',
                'last_name': 'Last',
                'email': 'new_test@test.test',
                'comments': 'Please give me an account',
            },
        )

        user = User.objects.get(email='new_test@test.test')

        self.assertEqual(user.registration_method, 'REQ')
        self.assertIsNotNone(user.applied_datetime)
        self.assertEqual(user.application_comments, 'Please give me an account')

    #~ TODO: def test_notification_emails_are_sent_to_moderators(self):

    def test_request_invitation_redirect(self):
        response = self.client.post(
            reverse('accounts:request-invitation'),
            data = {
                'first_name': 'First',
                'last_name': 'Last',
                'email': 'new_test@test.test',
                'comments': 'Please give me an account',
            },
        )

        self.assertRedirects(response, '/accounts/request-invitation/done')


class ActivateAccountTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.invited_user = InvitedPendingFactory(
            email='validuser@test.test',
            auth_token='mytoken',
        )
        self.invalid_invited_user = InvitedPendingFactory(
            auth_token = 'invalid',
            auth_token_is_used = True,
        )

    def test_activate_account_url_resolves_to_activate_account_view(self):
        url = resolve('/accounts/activate/mytoken')

        self.assertEqual(url.func, activate_account)

    def test_activate_account_view_with_valid_token(self):
        response = self.client.get('/accounts/activate/mytoken')

        self.assertEqual(response.status_code, 200)


    def test_raises_404_if_given_token_not_attached_to_a_user(self):
        response = self.client.get('/accounts/activate/notoken')

        self.assertEqual(response.status_code, 404)

    def test_form_shows_if_token_is_valid(self):
        response = self.client.get('/accounts/activate/mytoken')
        expected_html = '<legend>Activate Account</legend>'

        self.assertInHTML(expected_html, response.content.decode())

    def test_error_shows_if_token_is_invalid(self):
        response = self.client.get('/accounts/activate/invalid')
        expected_html = '<h3 class="lined">Token is Used</h3>'

        self.assertInHTML(expected_html, response.content.decode())

    def test_can_activate_account(self):

        user = User.objects.get(email='validuser@test.test')
        old_pass = user.password

        self.client.post(
            '/accounts/activate/mytoken',
            data = {
                'first_name': 'Hello',
                'last_name': 'There',
                'password': 'abc',
                'confirm_password': 'abc',
            },
        )

        user = User.objects.get(email='validuser@test.test')

        self.assertEqual(user.first_name, 'Hello')
        self.assertEqual(user.last_name, 'There')
        self.assertNotEqual(user.password, old_pass)
        self.assertTrue(user.is_active)
        self.assertTrue(user.auth_token_is_used)


    def test_activated_account_redirects_to_correct_view(self):
        response = self.client.post(
            '/accounts/activate/mytoken',
            data = {
                'first_name': 'Hello',
                'last_name': 'There',
                'password': 'abc',
                'confirm_password': 'abc',
            },
        )

        #~ TODO: check 'show welcome' session here
        self.assertRedirects(response, '/')


class ProfileSettingsTest(TestCase):

    def setUp(self):

        self.standard_user = UserFactory()
        self.client = Client()

    def test_profile_url_resolves_to_profile_settings_view(self):
        url = resolve('/accounts/profile')

        self.assertEqual(url.func, profile_settings)

    def test_profile_is_not_available_to_unauthenticated_users(self):
        response = self.client.get(reverse('accounts:profile-settings'))

        #Unauthenticated user is redirected to login page
        self.assertRedirects(
            response,
            '/accounts/login/?next=/accounts/profile',
            status_code=302
        )

    def test_profile_is_available_to_authenticated_users(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.get(reverse('accounts:profile-settings'))

        self.assertEqual(response.status_code, 200)

    #~def test_can_update_profile(self):
    #~def test_link_is_correctly_matched_to_brand(self):


class AccountSettingsTest(TestCase):
    def setUp(self):

        self.standard_user = UserFactory()
        self.client = Client()

    def test_account_settings_url_resolves_to_account_settings_view(self):
        url = resolve('/accounts/settings')

        self.assertEqual(url.func, account_settings)

    def test_account_settings_is_not_available_to_unauthenticated_users(self):
        response = self.client.get(reverse('accounts:account-settings'))

        #Unauthenticated user is redirected to login page
        self.assertRedirects(
            response,
            '/accounts/login/?next=/accounts/settings',
            status_code=302
        )

    def test_account_settings_is_available_to_authenticated_users(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.get(reverse('accounts:account-settings'))

        self.assertEqual(response.status_code, 200)

    def test_account_settings_form_is_rendered_to_page(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.get(reverse('accounts:account-settings'))
        expected_html = '<legend>Account Settings</legend>'

        self.assertInHTML(expected_html, response.content.decode())

    def test_close_account_form_is_rendered_to_page(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.get(reverse('accounts:account-settings'))
        expected_html = '<legend>Close Account</legend>'

        self.assertInHTML(expected_html, response.content.decode())

    def test_update_account_url_resolves_to_update_account_view(self):
        url = resolve('/accounts/settings/update')

        self.assertEqual(url.func, update_account)

    def test_update_account_not_available_to_unautheticated_users(self):
        response = self.client.post(
            reverse('accounts:update-account'),
            data = {
                'email': 'mynewemail@test.test',
            },
        )

        #Unauthenticated user is redirected to login page
        self.assertRedirects(
            response,
            '/accounts/login/?next=/accounts/settings/update',
            status_code=302
        )

    def test_update_account_not_available_without_POST_data(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.get(reverse('accounts:update-account'))

        self.assertEqual(response.status_code, 405)

    def test_update_account_is_available_to_authenticated_users_with_POST_data(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.post(
            reverse('accounts:update-account'),
            data = {
                'email': 'mynewemail@test.test',
            },
        )

        # Sending valid data should result in this view redirecting back
        # to account settings
        self.assertRedirects(
            response,
            '/accounts/settings',
            status_code=302
        )

    def test_can_update_email(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.post(
            reverse('accounts:update-account'),
            data = {
                'email': 'mynewemail@test.test',
            },
        )

        user = User.objects.get(id=self.standard_user.id)

        self.assertEqual(user.email, 'mynewemail@test.test')

    def test_can_update_password(self):
        self.client.login(username=self.standard_user.email, password='pass')
        old_pass = self.standard_user.password
        response = self.client.post(
            reverse('accounts:update-account'),
            data = {
                'email': self.standard_user.email,
                'current_password': 'pass',
                'reset_password': 'new',
                'reset_password_confirm': 'new',
            },
        )

        user = User.objects.get(id=self.standard_user.id)
        self.assertNotEqual(user.password, old_pass)

    def test_close_account_url_resolves_to_close_account_view(self):
        url = resolve('/accounts/close')

        self.assertEqual(url.func, close_account)


    def test_close_account_not_available_to_unautheticated_users(self):
        response = self.client.post(
            reverse('accounts:close-account'),
            data = {
                'password': 'pass',
            },
        )

        #Unauthenticated user is redirected to login page
        self.assertRedirects(
            response,
            '/accounts/login/?next=/accounts/close',
            status_code=302
        )

    def test_close_account_not_available_without_POST_data(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.get(reverse('accounts:close-account'))

        self.assertEqual(response.status_code, 405)

    def test_close_account_is_available_to_authenticated_users_with_POST_data(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.post(
            reverse('accounts:close-account'),
            data = {
                'password': 'pass',
            },
        )

        # Sending valid data should result in this view redirecting to done
        self.assertRedirects(
            response,
            '/accounts/close/done',
            status_code=302
        )

    def test_can_close_account(self):
        self.client.login(username=self.standard_user.email, password='pass')
        response = self.client.post(
            reverse('accounts:close-account'),
            data = {
                'password': 'pass',
            },
        )

        user = User.objects.get(id=self.standard_user.id)

        self.assertFalse(user.is_active)
        self.assertTrue(user.is_closed)
