from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import api, views

urlpatterns = [
    path("", views.mailbox_view, {"folder": "INBOX"}, name="mailbox"),
    path("folder/<str:folder>/", views.mailbox_view, name="mailbox_folder"),
    path("search/", views.search_view, name="search"),
    path("label/<int:pk>/", views.label_view, name="label"),
    path("labels/create/", views.label_create, name="label_create"),
    path("compose/", views.compose_view, name="compose"),
    path("contacts/", views.contacts_view, name="contacts"),
    path("settings/", views.settings_view, name="settings"),
    path("message/<int:pk>/", views.message_view, name="message"),
    path("message/<int:pk>/action/", views.message_action, name="message_action"),
    path("message/<int:pk>/raw/", views.message_raw, name="message_raw"),
    path("attachment/<int:pk>/", views.attachment_download, name="attachment"),
    path("login/", LoginView.as_view(template_name="mail/login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),

    # --- REST API ---
    path("api/folders/", api.folders),
    path("api/messages/", api.messages),
    path("api/messages/<int:pk>/", api.message_detail),
    path("api/messages/<int:pk>/action/", api.message_action),
    path("api/send/", api.send),
    path("api/contacts/", api.contacts),
]
