__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"

from django import forms
from django.utils.translation import ugettext_lazy as _

from submission import models
from core import models as core_models
from identifiers import models as ident_models
from review.forms import render_choices


class PublisherNoteForm(forms.ModelForm):

    class Meta:
        model = models.PublisherNote
        fields = ('text',)


class ArticleStart(forms.ModelForm):

    class Meta:
        model = models.Article
        fields = ('publication_fees', 'submission_requirements', 'copyright_notice', 'comments_editor',
                  'competing_interests')

    def __init__(self, *args, **kwargs):
        journal = kwargs.pop('journal', False)
        super(ArticleStart, self).__init__(*args, **kwargs)

        self.fields['competing_interests'].label = ''
        self.fields['comments_editor'].label = ''

        if not journal.submissionconfiguration.publication_fees:
            self.fields.pop('publication_fees')
        else:
            self.fields['publication_fees'].required = True

        if not journal.submissionconfiguration.submission_check:
            self.fields.pop('submission_requirements')
        else:
            self.fields['submission_requirements'].required = True

        if not journal.submissionconfiguration.copyright_notice:
            self.fields.pop('copyright_notice')
        else:
            self.fields['copyright_notice'].required = True

        if not journal.submissionconfiguration.competing_interests:
            self.fields.pop('competing_interests')

        if not journal.submissionconfiguration.comments_to_the_editor:
            self.fields.pop('comments_editor')


class ArticleInfo(forms.ModelForm):
    keywords = forms.CharField(required=False)

    class Meta:
        model = models.Article
        fields = ('title', 'subtitle', 'abstract', 'non_specialist_summary', 'language', 'section', 'license',
                  'primary_issue', 'page_numbers', 'is_remote', 'remote_url')
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': _('Title')}),
            'subtitle': forms.TextInput(attrs={'placeholder': _('Subtitle')}),
            'abstract': forms.Textarea(
                attrs={'placeholder': _('Enter your article\'s abstract here')}),
        }

    def __init__(self, *args, **kwargs):
        elements = kwargs.pop('additional_fields', None)
        submission_summary = kwargs.pop('submission_summary', None)
        journal = kwargs.pop('journal', None)

        super(ArticleInfo, self).__init__(*args, **kwargs)
        if 'instance' in kwargs:
            article = kwargs['instance']
            self.fields['section'].queryset = models.Section.objects.language().fallbacks('en').filter(
                journal=article.journal, public_submissions=True)
            self.fields['license'].queryset = models.Licence.objects.filter(journal=article.journal,
                                                                            available_for_submission=True)
            self.fields['section'].required = True
            self.fields['license'].required = True
            self.fields['primary_issue'].queryset = article.journal.issues()

            if submission_summary:
                self.fields['non_specialist_summary'].required = True

            # Pop fields based on journal.submissionconfiguration
            if journal:
                if not journal.submissionconfiguration.subtitle:
                    self.fields.pop('subtitle')

                if not journal.submissionconfiguration.abstract:
                    self.fields.pop('abstract')

                if not journal.submissionconfiguration.language:
                    self.fields.pop('language')

                if not journal.submissionconfiguration.license:
                    self.fields.pop('license')

                if not journal.submissionconfiguration.keywords:
                    self.fields.pop('keywords')

                if not journal.submissionconfiguration.section:
                    self.fields.pop('section')

            # Add additional fields
            if elements:
                for element in elements:
                    if element.kind == 'text':
                        self.fields[element.name] = forms.CharField(
                            widget=forms.TextInput(attrs={'div_class': element.width}),
                            required=element.required)
                    elif element.kind == 'textarea':
                        self.fields[element.name] = forms.CharField(widget=forms.Textarea,
                                                                    required=element.required)
                    elif element.kind == 'date':
                        self.fields[element.name] = forms.CharField(
                            widget=forms.DateInput(attrs={'class': 'datepicker', 'div_class': element.width}),
                            required=element.required)

                    elif element.kind == 'select':
                        choices = render_choices(element.choices)
                        self.fields[element.name] = forms.ChoiceField(
                            widget=forms.Select(attrs={'div_class': element.width}), choices=choices,
                            required=element.required)

                    elif element.kind == 'email':
                        self.fields[element.name] = forms.EmailField(
                            widget=forms.TextInput(attrs={'div_class': element.width}),
                            required=element.required)
                    elif element.kind == 'check':
                        self.fields[element.name] = forms.BooleanField(
                            widget=forms.CheckboxInput(attrs={'is_checkbox': True}),
                            required=element.required)

                    self.fields[element.name].help_text = element.help_text
                    self.fields[element.name].label = element.name

                    if article:
                        try:
                            check_for_answer = models.FieldAnswer.objects.get(field=element, article=article)
                            self.fields[element.name].initial = check_for_answer.answer
                        except models.FieldAnswer.DoesNotExist:
                            pass

    def save(self, commit=True, request=None):
        article = super(ArticleInfo, self).save(commit=False)

        posted_keywords = self.cleaned_data.get('keywords', '').split(',')
        for keyword in posted_keywords:
            if keyword != '':
                new_keyword, c = models.Keyword.objects.get_or_create(word=keyword)
                article.keywords.add(new_keyword)

        for keyword in article.keywords.all():
            if keyword.word not in posted_keywords:
                article.keywords.remove(keyword)

        if request:
            additional_fields = models.Field.objects.filter(journal=request.journal)

            for field in additional_fields:
                answer = request.POST.get(field.name, None)
                if answer:
                    try:
                        field_answer = models.FieldAnswer.objects.get(article=article, field=field)
                        field_answer.answer = answer
                        field_answer.save()
                    except models.FieldAnswer.DoesNotExist:
                        field_answer = models.FieldAnswer.objects.create(article=article, field=field, answer=answer)

            request.journal.submissionconfiguration.handle_defaults(article)

        if commit:
            article.save()

        return article


class AuthorForm(forms.ModelForm):

    class Meta:
        model = core_models.Account
        exclude = (
            'date_joined',
            'activation_code'
            'date_confirmed'
            'confirmation_code'
            'reset_code'
            'reset_code_validated'
            'roles'
            'interest'
            'is_active'
            'is_staff'
            'is_admin'
            'password',
            'username',
            'roles',

        )

        widgets = {
            'first_name': forms.TextInput(attrs={'placeholder': 'First name'}),
            'middle_name': forms.TextInput(attrs={'placeholder': 'Middle name'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Last name'}),
            'biography': forms.Textarea(
                attrs={'placeholder': 'Enter biography here'}),
            'institution': forms.TextInput(attrs={'placeholder': 'Institution'}),
            'department': forms.TextInput(attrs={'placeholder': 'Department'}),
            'twitter': forms.TextInput(attrs={'placeholder': 'Twitter handle'}),
            'linkedin': forms.TextInput(attrs={'placeholder': 'LinkedIn profile'}),
            'impactstory': forms.TextInput(attrs={'placeholder': 'ImpactStory profile'}),
            'orcid': forms.TextInput(attrs={'placeholder': 'ORCID ID'}),
            'email': forms.TextInput(attrs={'placeholder': 'Email address'}),

        }

    def __init__(self, *args, **kwargs):
        super(AuthorForm, self).__init__(*args, **kwargs)
        self.fields['password'].required = False
        self.fields['first_name'].required = True
        self.fields['last_name'].required = True


class FileDetails(forms.ModelForm):

    class Meta:
        model = core_models.File
        fields = (
            'label',
            'description',
        )

    def __init__(self, *args, **kwargs):
        super(FileDetails, self).__init__(*args, **kwargs)
        self.fields['label'].required = True
        self.fields['label'].inital = 'Manuscript'


class EditFrozenAuthor(forms.ModelForm):

    class Meta:
        model = models.FrozenAuthor
        fields = (
            'first_name',
            'middle_name',
            'last_name',
            'institution',
            'department',
            'country',
        )


class IdentifierForm(forms.ModelForm):

    class Meta:
        model = ident_models.Identifier
        fields = (
            'id_type',
            'identifier',
            'enabled',
        )


class FieldForm(forms.ModelForm):

    class Meta:
        model = models.Field
        exclude = (
            'journal',
            'press',
        )


class LicenseForm(forms.ModelForm):

    class Meta:
        model = models.Licence
        exclude = (
            'journal',
            'press',
        )


class ConfiguratorForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(ConfiguratorForm, self).__init__(*args, **kwargs)
        self.fields['default_section'].queryset = models.Section.objects.filter(journal=self.instance.journal)
        self.fields['default_license'].queryset = models.Licence.objects.filter(journal=self.instance.journal)

    class Meta:
        model = models.SubmissionConfiguration
        exclude = (
            'journal',
        )
