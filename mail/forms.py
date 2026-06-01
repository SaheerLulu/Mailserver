from django import forms


class ComposeForm(forms.Form):
    to = forms.CharField(
        label="To",
        widget=forms.TextInput(attrs={"placeholder": "comma-separated addresses"}),
    )
    cc = forms.CharField(label="Cc", required=False)
    subject = forms.CharField(label="Subject", required=False)
    body = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 14}))
    in_reply_to = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_to(self):
        value = self.cleaned_data["to"]
        if not any(part.strip() for part in value.replace(";", ",").split(",")):
            raise forms.ValidationError("Enter at least one recipient.")
        return value
