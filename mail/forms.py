from django import forms

from .models import Mailbox


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Mailbox
        fields = ["full_name", "signature", "spam_threshold",
                  "vacation_enabled", "vacation_subject", "vacation_message",
                  "webhook_url"]
        widgets = {
            "signature": forms.Textarea(attrs={"rows": 4}),
            "vacation_message": forms.Textarea(attrs={"rows": 4}),
        }


class ComposeForm(forms.Form):
    to = forms.CharField(
        label="To", required=False,
        widget=forms.TextInput(attrs={"placeholder": "comma-separated addresses"}),
    )
    cc = forms.CharField(label="Cc", required=False)
    subject = forms.CharField(label="Subject", required=False)
    body = forms.CharField(label="Message", required=False,
                           widget=forms.Textarea(attrs={"rows": 14}))
    body_html = forms.CharField(required=False, widget=forms.HiddenInput)
    in_reply_to = forms.CharField(required=False, widget=forms.HiddenInput)
    references = forms.CharField(required=False, widget=forms.HiddenInput)
    draft_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    # Optional "schedule send" value (datetime-local string, parsed in delivery).
    send_at = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, require_recipient=True, **kwargs):
        self._require_recipient = require_recipient
        super().__init__(*args, **kwargs)

    def clean_to(self):
        value = self.cleaned_data.get("to", "")
        if self._require_recipient and not any(
                part.strip() for part in value.replace(";", ",").split(",")):
            raise forms.ValidationError("Enter at least one recipient.")
        return value
