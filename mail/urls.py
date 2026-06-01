from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.mailbox_view, {"folder": "INBOX"}, name="mailbox"),
    path("folder/<str:folder>/", views.mailbox_view, name="mailbox_folder"),
    path("compose/", views.compose_view, name="compose"),
    path("message/<int:pk>/", views.message_view, name="message"),
    path("message/<int:pk>/action/", views.message_action, name="message_action"),
    path("message/<int:pk>/raw/", views.message_raw, name="message_raw"),
    path("attachment/<int:pk>/", views.attachment_download, name="attachment"),
    path("login/", LoginView.as_view(template_name="mail/login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
