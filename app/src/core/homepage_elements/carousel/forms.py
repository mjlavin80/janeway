from django import forms
from django.contrib import messages

from core.homepage_elements.carousel import models
from submission import models as submission_models


class CarouselForm(forms.Form):
    """
    A form that modifies carousel settings for a journal.
    """

    carousel_mode = forms.ChoiceField(choices=models.Carousel.get_carousel_modes(), required=False)
    carousel_article_limit = forms.IntegerField(required=True, label='Maximum number of articles to show')
    carousel_news_limit = forms.IntegerField(required=True, label='Maximum number of news items to show')
    carousel_exclude = forms.BooleanField(required=False, label='Exclude selected articles')
    carousel_articles = forms.ModelMultipleChoiceField(
        queryset=submission_models.Article.objects.filter(stage=submission_models.STAGE_PUBLISHED), required=False)

    def __init__(self, *args, **kwargs):
        request = kwargs.pop('request', None)

        article_list = self.load(request)

        super(CarouselForm, self).__init__(*args, **kwargs)

        if request.site_type and request.site_type.carousel is not None:
            self.initial['carousel_mode'] = request.site_type.carousel.mode
            self.initial['carousel_exclude'] = request.site_type.carousel.exclude
            self.initial['carousel_articles'] = article_list
            self.initial['carousel_article_limit'] = request.site_type.carousel.article_limit
            self.initial['carousel_news_limit'] = request.site_type.carousel.news_limit

    def load(self, request):
        # if no carousel set, create one
        if request.site_type and request.site_type.carousel is None:
            return []

        articles = request.site_type.carousel.articles.all()
        article_list = []

        for article in articles:
            article_list.append(article.pk)

        return article_list

    def save(self, request, commit=True):
        mode = self.cleaned_data["carousel_mode"]
        exclude = self.cleaned_data["carousel_exclude"]
        article_limit = self.cleaned_data["carousel_article_limit"]
        news_limit = self.cleaned_data["carousel_news_limit"]
        list_of_articles = self.cleaned_data["carousel_articles"]

        if request.site_type.carousel is None:
            carousel = models.Carousel.objects.create()
            request.site_type.carousel = carousel
            request.site_type.carousel.save()
            request.site_type.save()

        request.site_type.carousel.mode = mode
        request.site_type.carousel.article_limit = article_limit
        request.site_type.carousel.news_limit = news_limit

        if mode == "selected" or mode == "mixed-selected":
            # you can't use the same list of articles to include and exclude
            exclude = False

        request.site_type.carousel.exclude = exclude

        request.site_type.carousel.articles.clear()

        for article in list_of_articles:
            request.site_type.carousel.articles.add(article)

        if commit:
            request.site_type.carousel.save()
            request.site_type.save()

        messages.add_message(request, messages.SUCCESS, 'Journal carousel settings saved')

    def clean(self):
        cleaned_data = self.cleaned_data

        return cleaned_data
