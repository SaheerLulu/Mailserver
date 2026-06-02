from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path, re_path

from . import api, dav, oauth, views

urlpatterns = [
    path("", views.mailbox_view, {"folder": "INBOX"}, name="mailbox"),
    path("folder/<str:folder>/", views.mailbox_view, name="mailbox_folder"),
    path("search/", views.search_view, name="search"),
    path("snoozed/", views.snoozed_view, name="snoozed"),
    path("scheduled/", views.scheduled_view, name="scheduled"),
    path("scheduled/<int:pk>/cancel/", views.cancel_scheduled, name="cancel_scheduled"),
    path("label/<int:pk>/", views.label_view, name="label"),
    path("labels/create/", views.label_create, name="label_create"),
    path("compose/", views.compose_view, name="compose"),
    path("bulk/", views.bulk_action, name="bulk_action"),
    path("folder/<str:folder>/read-all/", views.mark_all_read, name="mark_all_read"),
    path("autocomplete/", views.autocomplete, name="autocomplete"),
    path("contacts/", views.contacts_view, name="contacts"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("settings/", views.settings_view, name="settings"),
    path("message/<int:pk>/", views.message_view, name="message"),
    path("message/<int:pk>/action/", views.message_action, name="message_action"),
    path("message/<int:pk>/raw/", views.message_raw, name="message_raw"),
    path("attachment/<int:pk>/", views.attachment_download, name="attachment"),
    path("login/", LoginView.as_view(template_name="mail/login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),

    # --- Web Push ---
    path("sw.js", views.service_worker, name="service_worker"),
    path("push/vapid-public-key/", views.vapid_public_key, name="vapid_key"),
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),

    # --- REST API ---
    path("api/folders/", api.folders),
    path("api/messages/", api.messages),
    path("api/messages/<int:pk>/", api.message_detail),
    path("api/messages/<int:pk>/action/", api.message_action),
    path("api/send/", api.send),
    path("api/contacts/", api.contacts),

    # --- OAuth2 ---
    path("oauth/token", oauth.token, name="oauth_token"),

    # --- CalDAV / CardDAV ---
    path(".well-known/carddav", dav.dav),
    path(".well-known/caldav", dav.dav),
    re_path(r"^dav/?(?P<path>.*)$", dav.dav, name="dav"),
]
