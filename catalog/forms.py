import re
from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError

from .client import normalize_base_url
from .models import Server

CHECKSUM_RE = re.compile(r"^[a-f0-9]{64}$")
SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE)
SIZE_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def validate_tag(value):
    if not value or len(value) > 64 or "\r" in value or "\n" in value:
        raise ValidationError("A tag is 1-64 characters on a single line.")


class TagField(forms.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 64)
        super().__init__(**kwargs)
        self.validators.append(validate_tag)


class ByteSizeField(forms.CharField):
    """Accepts `1048576`, `500 MB` or `1.5g`. Units are powers of 1024, as displayed in the UI."""

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return None
        match = SIZE_RE.match(value)
        if not match:
            raise ValidationError("Enter a size such as 1048576, 500 MB or 1.5 GB.")
        return int(float(match[1]) * SIZE_UNITS[match[2].lower()])


class ServerForm(forms.ModelForm):
    token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="Bearer token. Stored unencrypted in the database. Leave empty to keep the current one.",
    )

    class Meta:
        model = Server
        fields = ["name", "base_url", "token"]
        labels = {"base_url": "API URL"}
        help_texts = {"base_url": "For example http://host:8080/api/v1"}

    def clean_base_url(self):
        url = self.cleaned_data["base_url"]
        parts = urlsplit(url.strip())
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValidationError("Enter an http:// or https:// URL.")
        return normalize_base_url(url)

    def save(self, commit=True):
        server = super().save(commit=False)
        new_token = self.cleaned_data.get("token")
        server.token = new_token if new_token else self.initial.get("token", "")
        if commit:
            server.save()
        return server


class TitleForm(forms.Form):
    title = forms.CharField(max_length=256, required=False, strip=True)
    revision = forms.CharField(max_length=128)


class AddTagForm(forms.Form):
    tag = TagField()
    revision = forms.CharField(max_length=128)


class RemoveTagForm(forms.Form):
    tag = TagField()
    revision = forms.CharField(max_length=128)


class PoolRenameTagForm(forms.Form):
    old = TagField()
    new = TagField()

    def clean(self):
        data = super().clean()
        # Rename is add-then-remove, so an unchanged name would delete the tag.
        if data.get("old") and data.get("old") == data.get("new"):
            raise ValidationError("The new name is the same as the old one.")
        return data


class RenameTagForm(PoolRenameTagForm):
    revision = forms.CharField(max_length=128)


class DeletePoolTagForm(forms.Form):
    old = TagField()


def parse_attributes(text):
    attributes = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise ValidationError(f"Line {number} is not in the form key = value.")
        attributes[key.strip()] = value.strip()
    return attributes


def format_attributes(attributes):
    return "\n".join(f"{key} = {value}" for key, value in attributes.items())


class LocalMetadataForm(forms.Form):
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    attributes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "location = Reykjavik"}),
        help_text="One key = value per line.",
    )

    def clean_attributes(self):
        return parse_attributes(self.cleaned_data["attributes"])


VERIFIED_CHOICES = [("", "Any"), ("true", "Verified"), ("false", "Not verified")]


class BundleFilterForm(forms.Form):
    title = forms.CharField(
        max_length=256, required=False, label="Title contains",
        widget=forms.TextInput(attrs={"data-key": "/", "data-key-label": "Search", "type": "search"}),
    )
    tag = TagField(required=False, widget=forms.TextInput(attrs={"list": "pool-tags"}))
    author = forms.CharField(max_length=256, required=False)
    verified = forms.ChoiceField(choices=VERIFIED_CHOICES, required=False)
    sort = forms.ChoiceField(
        required=False,
        choices=[
            ("title", "Title A-Z"),
            ("-title", "Title Z-A"),
            ("-created_at", "Newest"),
            ("created_at", "Oldest"),
        ],
    )


class BundleQueryForm(forms.Form):
    """Mirrors BundleQueryRequest. All supplied filters are combined with AND."""

    title = forms.CharField(
        max_length=256, required=False, label="Title contains",
        widget=forms.TextInput(attrs={"data-key": "/", "data-key-label": "Search", "type": "search"}),
    )
    author = forms.CharField(max_length=256, required=False)
    tags = forms.CharField(
        required=False, label="Tags (all required)", help_text="One per line.",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    verified = forms.ChoiceField(choices=VERIFIED_CHOICES, required=False)
    created_after = forms.DateTimeField(
        required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    created_before = forms.DateTimeField(
        required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    min_size = ByteSizeField(required=False, label="Minimum size", help_text="For example 500 MB.")
    max_size = ByteSizeField(required=False, label="Maximum size")
    checksums = forms.CharField(
        required=False, label="Bundle checksums", help_text="Separated by whitespace.",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    file_path = forms.CharField(max_length=4096, required=False, label="File path")
    path_match = forms.ChoiceField(
        required=False, label="File path match",
        choices=[("contains", "Contains"), ("exact", "Exact"), ("prefix", "Prefix"), ("suffix", "Suffix")],
    )
    file_checksum = forms.CharField(max_length=64, required=False, label="File checksum")
    sort = forms.ChoiceField(
        required=False,
        choices=[
            ("title", "Title A-Z"),
            ("-title", "Title Z-A"),
            ("-created_at", "Newest"),
            ("created_at", "Oldest"),
            ("-size_bytes", "Largest"),
            ("size_bytes", "Smallest"),
        ],
    )

    def clean_tags(self):
        tags = list(dict.fromkeys(line.strip() for line in self.cleaned_data["tags"].splitlines() if line.strip()))
        for tag in tags:
            validate_tag(tag)
        return tags

    def clean_checksums(self):
        checksums = list(dict.fromkeys(self.cleaned_data["checksums"].split()))
        for checksum in checksums:
            if not CHECKSUM_RE.match(checksum):
                raise ValidationError(f"{checksum[:70]} is not a lowercase SHA-256 checksum.")
        return checksums

    def clean_file_checksum(self):
        checksum = self.cleaned_data["file_checksum"].strip()
        if checksum and not CHECKSUM_RE.match(checksum):
            raise ValidationError("Enter a lowercase SHA-256 checksum.")
        return checksum

    def to_payload(self):
        """Build the API filters; empty when the user supplied none."""
        data = self.cleaned_data
        payload = {}
        for key in ("title", "author", "tags", "checksums"):
            if data[key]:
                payload[key] = data[key]
        if data["verified"]:
            payload["verified"] = data["verified"] == "true"
        for key in ("created_after", "created_before"):
            if data[key]:
                payload[key] = data[key].isoformat()
        if data["min_size"] is not None:
            payload["min_size_bytes"] = data["min_size"]
        if data["max_size"] is not None:
            payload["max_size_bytes"] = data["max_size"]
        file_query = {}
        if data["file_path"]:
            file_query["path"] = data["file_path"]
            file_query["path_match"] = data["path_match"] or "contains"
        if data["file_checksum"]:
            file_query["checksum"] = data["file_checksum"]
        if file_query:
            payload["file"] = file_query
        return payload
