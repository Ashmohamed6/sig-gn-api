from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from .serializers import EmailOrUsernameTokenObtainPairSerializer, ChangeOwnPasswordSerializer
from .throttles import AuthIdentifierRateThrottle
from .views import EmailOrUsernameTokenView, LoginView


class AuthIdentifierThrottleTests(SimpleTestCase):
    def test_cache_key_normalizes_identifier(self):
        throttle = AuthIdentifierRateThrottle()

        request = type("Req", (), {"data": {"username": "  TEST@EXAMPLE.COM  "}})()
        cache_key = throttle.get_cache_key(request, view=None)

        self.assertEqual(cache_key, "throttle_auth_identifier_test@example.com")

    def test_cache_key_returns_none_without_identifier(self):
        throttle = AuthIdentifierRateThrottle()

        request = type("Req", (), {"data": {"password": "x"}})()
        cache_key = throttle.get_cache_key(request, view=None)

        self.assertIsNone(cache_key)

    def test_login_and_token_views_register_identifier_throttle(self):
        factory = APIRequestFactory()
        drf_request = factory.post("/api/accounts/login/", {"username": "u", "password": "p"}, format="json")

        login_view = LoginView()
        login_view.request = login_view.initialize_request(drf_request)
        login_throttles = login_view.get_throttles()

        token_view = EmailOrUsernameTokenView()
        token_view.request = token_view.initialize_request(drf_request)
        token_throttles = token_view.get_throttles()

        self.assertTrue(any(isinstance(t, AuthIdentifierRateThrottle) for t in login_throttles))
        self.assertTrue(any(isinstance(t, AuthIdentifierRateThrottle) for t in token_throttles))


class TokenSerializerTests(SimpleTestCase):
    def test_validate_maps_email_to_username_before_super_validate(self):
        serializer = EmailOrUsernameTokenObtainPairSerializer()

        fake_user_model = Mock()
        fake_user_model.DoesNotExist = type("DoesNotExist", (Exception,), {})
        fake_user_model.objects.get.return_value = SimpleNamespace(username="resolved-user")

        attrs = {"username": "USER@EXAMPLE.COM", "password": "x"}

        with patch("accounts.serializers.get_user_model", return_value=fake_user_model):
            with patch(
                "rest_framework_simplejwt.serializers.TokenObtainPairSerializer.validate",
                side_effect=lambda payload: payload,
            ) as super_validate:
                result = serializer.validate(attrs)

        self.assertEqual(result["username"], "resolved-user")
        self.assertEqual(super_validate.call_args[0][0]["username"], "resolved-user")

    def test_validate_keeps_identifier_when_email_not_found(self):
        serializer = EmailOrUsernameTokenObtainPairSerializer()

        does_not_exist = type("DoesNotExist", (Exception,), {})
        fake_user_model = Mock()
        fake_user_model.DoesNotExist = does_not_exist
        fake_user_model.objects.get.side_effect = does_not_exist()

        attrs = {"username": "missing@example.com", "password": "x"}

        with patch("accounts.serializers.get_user_model", return_value=fake_user_model):
            with patch(
                "rest_framework_simplejwt.serializers.TokenObtainPairSerializer.validate",
                side_effect=lambda payload: payload,
            ):
                result = serializer.validate(attrs)

        self.assertEqual(result["username"], "missing@example.com")


class ChangeOwnPasswordSerializerTests(SimpleTestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.user = self.user_model(
            username="profile-user",
            email="profile@example.com",
        )
        self.user.set_password("OldPass123!")

    def test_rejects_invalid_current_password(self):
        serializer = ChangeOwnPasswordSerializer(
            data={
                "current_password": "BadPass123!",
                "new_password": "NewPass456!@#",
                "new_password_confirm": "NewPass456!@#",
            },
            context={"user": self.user},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("current_password", serializer.errors)

    def test_rejects_same_password_as_current(self):
        serializer = ChangeOwnPasswordSerializer(
            data={
                "current_password": "OldPass123!",
                "new_password": "OldPass123!",
                "new_password_confirm": "OldPass123!",
            },
            context={"user": self.user},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("new_password", serializer.errors)

    def test_accepts_valid_payload(self):
        serializer = ChangeOwnPasswordSerializer(
            data={
                "current_password": "OldPass123!",
                "new_password": "NewPass456!@#",
                "new_password_confirm": "NewPass456!@#",
            },
            context={"user": self.user},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
