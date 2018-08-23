__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"


from operator import itemgetter
import collections
import uuid
import os

from django.db import models
from django.utils import timezone
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.urls import reverse
from django.db.models.signals import post_save
from django.dispatch import receiver

from utils.function_cache import cache
from utils import setting_handler
from submission import models as submission_models
from core import models as core_models, workflow
from press import models as press_models

# Issue types
# Use "Issue" for regular issues (rolling or periodic)
# Use "Collection" for special collections
ISSUE_TYPES = [
    ('Issue', 'Issue'),
    ('Collection', 'Collection'),
]

fs = FileSystemStorage(location=settings.MEDIA_ROOT)


def cover_images_upload_path(instance, filename):
    try:
        filename = str(uuid.uuid4()) + '.' + str(filename.split('.')[1])
    except IndexError:
        filename = str(uuid.uuid4())

    path = "cover_images/"
    return os.path.join(path, filename)


def issue_large_image_path(instance, filename):
    try:
        filename = str(uuid.uuid4()) + '.' + str(filename.split('.')[1])
    except IndexError:
        filename = str(uuid.uuid4())

    path = "issues/{0}".format(instance.pk)
    return os.path.join(path, filename)


class Journal(models.Model):
    code = models.CharField(max_length=10)
    domain = models.CharField(max_length=255, default='www.example.com', unique=True)
    current_issue = models.ForeignKey('Issue', related_name='current_issue', null=True, blank=True,
                                      on_delete=models.SET_NULL)
    carousel = models.OneToOneField('carousel.Carousel', related_name='journal', null=True, blank=True)
    thumbnail_image = models.ForeignKey('core.File', null=True, blank=True, related_name='thumbnail_image',
                                        on_delete=models.SET_NULL)
    press_image_override = models.ForeignKey('core.File', null=True, blank=True, related_name='press_image_override')
    default_cover_image = models.ImageField(upload_to=cover_images_upload_path, null=True, blank=True, storage=fs)
    default_large_image = models.ImageField(upload_to=cover_images_upload_path, null=True, blank=True, storage=fs)
    header_image = models.ImageField(upload_to=cover_images_upload_path, null=True, blank=True, storage=fs)
    favicon = models.ImageField(upload_to=cover_images_upload_path, null=True, blank=True, storage=fs)
    description = models.TextField(null=True, blank=True, verbose_name="Journal Description")
    contact_info = models.TextField(null=True, blank=True, verbose_name="Contact Information")

    is_remote = models.BooleanField(default=False)
    remote_submit_url = models.URLField(blank=True, null=True)
    remote_view_url = models.URLField(blank=True, null=True)

    # Nav Items
    nav_home = models.BooleanField(default=True)
    nav_news = models.BooleanField(default=False)
    nav_articles = models.BooleanField(default=True)
    nav_issues = models.BooleanField(default=True)
    nav_contact = models.BooleanField(default=True)
    nav_start = models.BooleanField(default=True)
    nav_review = models.BooleanField(default=True)
    nav_sub = models.BooleanField(default=True)

    # Boolean to determine if this journal has XSLT file
    has_xslt = models.BooleanField(default=False)

    # Boolean to determine if this journal should be hdden from the press
    hide_from_press = models.BooleanField(default=False)

    # Display sequence on the Journals page
    sequence = models.PositiveIntegerField(default=0)

    # this has to be handled this way so that we can add migrations to press
    try:
        press_name = press_models.Press.get_press(None).name
    except BaseException:
        press_name = ''

    def __str__(self):
        return u'{0}: {1}'.format(self.code, self.domain)

    @staticmethod
    def override_cover(request, absolute=True):
        if request.journal.press_image_override:
            if absolute:
                return os.path.join(settings.BASE_DIR, 'files', 'journals', str(request.journal.pk),
                                    str(request.journal.press_image_override.uuid_filename))
            else:
                return os.path.join('files', 'journals', str(request.journal.pk),
                                    str(request.journal.press_image_override.uuid_filename))
        else:
            return None

    def get_setting(self, group_name, setting_name):
        return setting_handler.get_setting(group_name, setting_name, self, create=False).processed_value

    @property
    @cache(300)
    def name(self):
        try:
            return setting_handler.get_setting('general', 'journal_name', self, create=False, fallback='en').value
        except IndexError:
            self.name = 'Janeway Journal'
            return self.name

    @name.setter
    def name(self, value):
        setting_handler.save_setting('general', 'journal_name', self, value)

    @property
    def publisher(self):
        return setting_handler.get_setting('general', 'publisher_name', self, create=False, fallback='en').value

    @publisher.setter
    def publisher(self, value):
        setting_handler.save_setting('general', 'publisher_name', self, value)

    @property
    @cache(120)
    def issn(self):
        return setting_handler.get_setting('general', 'journal_issn', self, create=False, fallback='en').value

    @property
    @cache(120)
    def use_crossref(self):
        try:
            return setting_handler.get_setting('Identifiers',
                                               'crossref_prefix',
                                               self,
                                               create=False,
                                               fallback='en').processed_value
        except IndexError:
            return False

    @issn.setter
    def issn(self, value):
        setting_handler.save_setting('general', 'journal_issn', self, value)

    @property
    def slack_logging_enabled(self):
        slack_webhook = setting_handler.get_setting('general', 'slack_webhook', self).value
        slack_logging = setting_handler.get_setting('general', 'slack_logging', self).processed_value

        if slack_logging and slack_webhook:
            return True
        else:
            return False

    @property
    def press(self):
        press = press_models.Press.objects.all()[0]
        return press

    def full_url(self, request=None):
        if not request:
            return self.requestless_url()

        return 'http{0}://{1}{2}'.format(
            's' if request.is_secure() else '',
            self.domain if settings.URL_CONFIG == 'domain' else self.press.domain,
            ':{0}'.format(request.port) if (request != 80 or request.port == 443) and settings.DEBUG else '',
            '{0}{1}'.format('/' if settings.URL_CONFIG == 'path' else '',
                            self.code if settings.URL_CONFIG == 'path' else '')
        )

    def full_reverse(self, request, url_name, kwargs):
        base_url = self.full_url(request)
        url_path = reverse(url_name, kwargs=kwargs)
        return "{0}{1}".format(base_url, url_path)

    def requestless_url(self):
        from core.middleware import GlobalRequestMiddleware
        local_request = GlobalRequestMiddleware.get_current_request()

        if local_request.journal:
            return local_request.journal_base_url
        else:
            return local_request.press_base_url

    def next_issue_order(self):
        issue_orders = [issue.order for issue in Issue.objects.filter(journal=self)]
        return max(issue_orders) + 1 if issue_orders else 0

    def issues(self):
        return Issue.objects.filter(journal=self)

    def editors(self):
        pks = [role.user.pk for role in core_models.AccountRole.objects.filter(role__slug='editor', journal=self)]
        return core_models.Account.objects.filter(pk__in=pks)

    def users_with_role(self, role):
        pks = [role.user.pk for role in core_models.AccountRole.objects.filter(role__slug=role, journal=self)]
        return core_models.Account.objects.filter(pk__in=pks)

    def editor_pks(self):
        return [[str(role.user.pk), str(role.user.pk)] for role in
                core_models.AccountRole.objects.filter(role__slug='editor', journal=self)]

    def journal_users(self, objects=True):
        if objects:
            users = [role.user for role in core_models.AccountRole.objects.filter(journal=self, user__is_active=True)]
        else:
            users = [role.user.pk for role in core_models.AccountRole.objects.filter(journal=self, user__is_active=True)]

        return set(users)

    @cache(300)
    def editorial_groups(self):
        return core_models.EditorialGroup.objects.filter(journal=self)

    @property
    def editor_emails(self):
        editor_roles = core_models.AccountRole.objects.filter(role__slug='editor', journal=self)
        return [role.user.email for role in editor_roles]

    def next_featured_article_order(self):
        orderings = [featured_article.sequence for featured_article in self.featuredarticle_set.all()]
        return max(orderings) + 1 if orderings else 0

    def next_contact_order(self):
        contacts = core_models.Contacts.objects.filter(content_type__model='journal', object_id=self.pk)
        orderings = [contact.sequence for contact in contacts]
        return max(orderings) + 1 if orderings else 0

    def next_group_order(self):
        orderings = [group.sequence for group in self.editorialgroup_set.all()]
        return max(orderings) + 1 if orderings else 0

    @property
    def scss_files(self):
        import journal.logic as journal_logic
        return journal_logic.list_scss(self)

    @property
    def active_carousel(self):
        """ Renders a carousel for the journal homepage.
        :return: a tuple containing the active carousel and list of associated articles
        """
        import core.logic as core_logic
        carousel_objects = []
        article_objects = []
        news_objects = []

        if self.carousel is None:
            return None, []

        if self.carousel.mode == 'off':
            return self.carousel, []

        # determine the carousel mode and build the list of objects as appropriate
        if self.carousel.mode == "latest":
            article_objects = core_logic.latest_articles(self.carousel, 'journal')

        elif self.carousel.mode == "selected":
            article_objects = core_logic.selected_articles(self.carousel, 'journal')

        elif self.carousel.mode == "news":
            news_objects = core_logic.news_items(self.carousel, 'journal')

        elif self.carousel.mode == "mixed":
            # news items and latest articles
            news_objects = core_logic.news_items(self.carousel, 'journal')
            article_objects = core_logic.latest_articles(self.carousel, 'journal')

        elif self.carousel.mode == "mixed-selected":
            # news items and latest articles
            news_objects = core_logic.news_items(self.carousel, 'journal')
            article_objects = core_logic.selected_articles(self.carousel)

        # run the exclusion routine
        if self.carousel.mode != "news" and self.carousel.exclude:
            # remove articles from the list here when the user has specified that certain articles
            # should be excluded
            exclude_list = self.carousel.articles.all()
            excluded = exclude_list.values_list('id', flat=True)
            try:
                article_objects = article_objects.exclude(id__in=excluded)
            except AttributeError:
                for exclude_item in exclude_list:
                    if exclude_item in article_objects:
                        article_objects.remove(exclude_item)

        # now limit the items by the respective amounts
        if self.carousel.article_limit > 0:
            article_objects = article_objects[:self.carousel.article_limit]

        if self.carousel.news_limit > 0:
            news_objects = news_objects[:self.carousel.news_limit]

        # if running in a mixed mode, sort the objects by a mixture of date_published for articles and posted for
        # news items. Note, this has to be done AFTER the exclude procedure above.
        if self.carousel.mode == "mixed-selected" or self.carousel.mode == 'mixed':
            carousel_objects = core_logic.sort_mixed(article_objects, news_objects)
        elif self.carousel.mode == 'news':
            carousel_objects = news_objects
        else:
            carousel_objects = article_objects

        return self.carousel, carousel_objects

    def next_pa_seq(self):
        "Works out what the next pinned article sequence should be."
        pinned_articles = PinnedArticle.objects.filter(journal=self).reverse()
        if pinned_articles:
            return pinned_articles[0].sequence + 1
        else:
            return 0

    def setup_directory(self):
        directory = os.path.join(settings.BASE_DIR, 'files', 'journals', str(self.pk))
        if not os.path.exists(directory):
            os.makedirs(directory)

    def workflow(self):
        try:
            return core_models.Workflow.objects.get(journal=self)
        except core_models.Workflow.DoesNotExist:
            return workflow.create_default_workflow(self)

    def element_in_workflow(self, element_name):
        try:
            element = core_models.WorkflowElement.objects.get(element_name=element_name, journal=self)
            if element in self.workflow().elements.all():
                return True
            else:
                return False
        except core_models.WorkflowElement.DoesNotExist:
            return False


class PinnedArticle(models.Model):
    journal = models.ForeignKey(Journal)
    article = models.ForeignKey('submission.Article')
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('sequence',)

    def __str__(self):
        return '{0}, {1}: {2}'.format(self.sequence, self.journal.code, self.article.title)


class Issue(models.Model):
    journal = models.ForeignKey(Journal)

    # issue metadata
    volume = models.IntegerField(default=1)
    issue = models.IntegerField(default=1)
    issue_title = models.CharField(blank=True, max_length=300)
    date = models.DateTimeField(default=timezone.now)
    order = models.IntegerField(default=1)
    issue_type = models.CharField(max_length=200, blank=False, null=False, default='Issue', choices=ISSUE_TYPES)
    issue_description = models.TextField()

    cover_image = models.ImageField(upload_to=cover_images_upload_path, null=True, blank=True, storage=fs)
    large_image = models.ImageField(upload_to=issue_large_image_path, null=True, blank=True, storage=fs)

    # issue articles
    articles = models.ManyToManyField('submission.Article', blank=True, null=True, related_name='issues')

    # guest editors
    guest_editors = models.ManyToManyField('core.Account', blank=True, null=True, related_name='guest_editors')

    class Meta:
        ordering = ('year', 'volume', 'issue', 'title')

    @property
    def display_title(self):
        if self.issue_title:
            return self.issue_title
        else:
            return u'Volume {0}, Issue {1} ({2})'.format(self.volume, self.issue, self.date.year)

    @property
    def manage_issue_list(self):
        section_article_dict = collections.OrderedDict()

        article_pks = [article.pk for article in self.articles.all()]

        # Find explicitly ordered sections for this issue
        ordered_sections = SectionOrdering.objects.filter(issue=self)

        for ordered_section in ordered_sections:
            article_list = list()

            ordered_articles = ArticleOrdering.objects.filter(issue=self, section=ordered_section.section)

            for article in ordered_articles:
                article_list.append(article)

            articles = self.articles.filter(section=ordered_section.section, pk__in=article_pks)

            for article in articles:
                if not article in article_list:
                    article_list.append(article)

            section_article_dict[ordered_section.section] = article_list

        # Handle all other sections that have articles in this issue.
        for article in self.articles.all():
            if not section_article_dict.get(article.section):
                article_list = list()
                articles = self.articles.filter(section=article.section, pk__in=article_pks).order_by('section')

                for article in articles:
                    article_list.append(article)

                section_article_dict[article.section] = article_list

        return section_article_dict

    @property
    def all_sections(self):
        ordered_sections = [order.section for order in SectionOrdering.objects.filter(issue=self)]
        articles = self.articles.all().order_by('section')

        for article in articles:
            if not article.section in ordered_sections:
                ordered_sections.append(article.section)

        return ordered_sections

    @property
    def first_section(self):
        all_sections = self.all_sections

        if all_sections:
            return all_sections[0]
        else:
            return 0

    @property
    def last_section(self):
        all_sections = self.all_sections

        if all_sections:
            return all_sections[-1]
        else:
            return 0

    @property
    def issue_articles(self):
        # this property should be used to display article ToCs since it enforces visibility of Published items
        articles = self.articles.filter(stage=submission_models.STAGE_PUBLISHED)

        ordered_list = list()
        for article in articles:
            ordered_list.append({'article': article, 'order': self.get_article_order(article)})

        return sorted(ordered_list, key=itemgetter('order'))

    def structure(self):
        structure = collections.OrderedDict()

        sections = self.all_sections
        articles = self.articles.all()

        for section in sections:
            article_with_order = ArticleOrdering.objects.filter(issue=self, section=section)

            article_list = list()
            for order in article_with_order:
                article_list.append(order.article)

            for article in articles.filter(section=section):
                if not article in article_list:
                    article_list.append(article)
            structure[section] = article_list

        return structure

    @property
    def article_pks(self):
        return [article.pk for article in self.articles.all()]

    def get_article_order(self, article):
        try:
            try:
                ordering = ArticleOrdering.objects.get(article=article, issue=self)
                return ordering.order
            except ArticleOrdering.MultipleObjectsReturned:
                orderings = ArticleOrdering.objects.filter(article=article, issue=self)
                return orderings[0]
        except ArticleOrdering.DoesNotExist:
            return 0

    def next_order(self):
        orderings = [ordering.order for ordering in ArticleOrdering.objects.filter(issue=self)]
        return max(orderings) + 1 if orderings else 0

    def __str__(self):
        return u'{0}: {1} {2} ({3})'.format(self.volume, self.issue, self.issue_title, self.date.year)

    class Meta:
        ordering = ("order", "-date")


class SectionOrdering(models.Model):
    section = models.ForeignKey('submission.Section')
    issue = models.ForeignKey(Issue)
    order = models.PositiveIntegerField(default=1)

    def __str__(self):
        return "{0}: {1}, ({2} {3}) {4}".format(self.order, self.issue.issue_title, self.issue.volume, self.issue.issue, self.section)

    class Meta:
        ordering = ('order', 'section')


class ArticleOrdering(models.Model):
    article = models.ForeignKey('submission.Article')
    issue = models.ForeignKey(Issue)
    section = models.ForeignKey('submission.Section')
    order = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ('article', 'issue', 'section')
        ordering = ('order', 'section')

    def __str__(self):
        return "{0}: {1}, {2}".format(self.order, self.section, self.article.title)


class FixedPubCheckItems(models.Model):
    article = models.OneToOneField('submission.Article')

    metadata = models.BooleanField(default=False)
    verify_doi = models.BooleanField(default=False)
    select_issue = models.BooleanField(default=False)
    set_pub_date = models.BooleanField(default=False)
    notify_the_author = models.BooleanField(default=False)
    select_render_galley = models.BooleanField(default=False)
    select_article_image = models.BooleanField(default=False)


class PresetPublicationCheckItem(models.Model):
    journal = models.ForeignKey(Journal)

    title = models.TextField()
    text = models.TextField()
    enabled = models.BooleanField(default=True)


class PrePublicationChecklistItem(models.Model):
    article = models.ForeignKey('submission.Article')

    completed = models.BooleanField(default=False)
    completed_by = models.ForeignKey('core.Account', blank=True, null=True)
    completed_on = models.DateTimeField(blank=True, null=True)

    title = models.TextField()
    text = models.TextField()

    def __str__(self):
        return "{0} - {1}".format(self.pk, self.title)


class BannedIPs(models.Model):
    ip = models.GenericIPAddressField()
    date_banned = models.DateField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Banned IPs"


def notification_type():
    return (
        ('submission', 'Submission'),
        ('acceptance', 'Acceptance'),
    )


class Notifications(models.Model):
    journal = models.ForeignKey(Journal)
    user = models.ForeignKey('core.Account')
    domain = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=notification_type())
    active = models.BooleanField(default=False)

    def __str__(self):
        return '{0}, {1}: {2}'.format(self.journal, self.user, self.domain)


# Signals
@receiver(post_save, sender=Journal)
def create_sites_folder(sender, instance, created, **kwargs):
    path = os.path.join(settings.BASE_DIR, 'sites', instance.code)
    if created:
        if not os.path.exists(path):
            os.makedirs(path)

        from submission.models import Section
        Section.objects.language('en').get_or_create(journal=instance,
                                                     number_of_reviewers=2,
                                                     name='Article',
                                                     plural='Articles')


@receiver(post_save, sender=Journal)
def setup_default_workflow(sender, instance, created, **kwargs):
    if created:
        workflow.create_default_workflow(instance)


@receiver(post_save, sender=Journal)
def setup_default_form(sender, instance, created, **kwargs):
    if created:
        from review import models as review_models

        if not review_models.ReviewForm.objects.filter(slug='default-form', journal=instance).exists():

            default_review_form = review_models.ReviewForm.objects.create(
                journal=instance,
                name='Default Form',
                slug='default-form',
                intro='Please complete the form below.',
                thanks='Thank you for completing the review.'
            )

            main_element = review_models.ReviewFormElement.objects.create(
                name='Review',
                kind='textarea',
                required=True,
                order=1,
                width='large-12 columns',
                help_text='Please add as much detail as you can.'
            )

            default_review_form.elements.add(main_element)
