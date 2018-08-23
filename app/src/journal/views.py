__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"

import json
import os
from shutil import copyfile
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.contrib.staticfiles.templatetags.staticfiles import static
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.urls import reverse
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.core.management import call_command

from cms import models as cms_models
from core import files, models as core_models, plugin_loader
from journal import logic, models, issue_forms, forms
from journal.logic import list_galleys
from metrics.logic import store_article_access
from review import forms as review_forms
from security.decorators import article_stage_accepted_or_later_required, \
    article_stage_accepted_or_later_or_staff_required, article_exists, file_user_required, has_request, has_journal, \
    file_history_user_required, file_edit_user_required, production_user_or_editor_required, \
    editor_user_required
from submission import models as submission_models
from utils import models as utils_models, shared
from events import logic as event_logic


@has_journal
def home(request):
    """ Renders a journal homepage.

    :param request: the request associated with this call
    :return: a rendered template of the journal homepage
    """
    issues_objects = models.Issue.objects.filter(journal=request.journal)
    sections = submission_models.Section.objects.filter(journal=request.journal)

    homepage_elements = core_models.HomepageElement.objects.filter(content_type=request.model_content_type,
                                                                   object_id=request.journal.pk,
                                                                   active=True).order_by('sequence')

    template = 'journal/index.html'
    context = {
        'homepage_elements': homepage_elements,
        'issues': issues_objects,
        'sections': sections,
    }

    # call all registered plugin block hooks to get relevant contexts
    for hook in settings.PLUGIN_HOOKS.get('yield_homepage_element_context', []):
        hook_module = plugin_loader.import_module(hook.get('module'))
        function = getattr(hook_module, hook.get('function'))
        element_context = function(request, homepage_elements)

        for k, v in element_context.items():
            context[k] = v

    return render(request, template, context)


@has_journal
def serve_journal_cover(request):
    """ Serves the cover image for this journal or, if not affiliated with a journal, serves the press logo.

    :param request: the request associated with this call
    :return: a streaming response of the retrieved image file
    """
    if not request.journal:
        # URL accessed from press site so serve press cover
        response = files.serve_press_cover(request, request.press.thumbnail_image)

        return response

    if not request.journal.thumbnail_image:
        logic.install_cover(request.journal, request)

    response = files.serve_journal_cover(request, request.journal.thumbnail_image)

    return response


@has_journal
def articles(request):
    """ Renders the list of articles in the journal.

    :param request: the request associated with this call
    :return: a rendered template of all articles
    """
    if request.POST and 'clear' in request.POST:
        return logic.unset_article_session_variables(request)

    sections = submission_models.Section.objects.language().fallbacks('en').filter(journal=request.journal,
                                                                                   is_filterable=True)
    page, show, filters, sort, redirect, active_filters = logic.handle_article_controls(request, sections)

    if redirect:
        return redirect

    pinned_articles = [pin.article for pin in models.PinnedArticle.objects.filter(
        journal=request.journal)]
    pinned_article_pks = [article.pk for article in pinned_articles]
    article_objects = submission_models.Article.objects.filter(journal=request.journal,
                                                               date_published__lte=timezone.now(),
                                                               section__pk__in=filters).prefetch_related(
        'frozenauthor_set').order_by(sort).exclude(
        pk__in=pinned_article_pks)

    paginator = Paginator(article_objects, show)

    try:
        articles = paginator.page(page)
    except PageNotAnInteger:
        articles = paginator.page(1)
    except EmptyPage:
        articles = paginator.page(paginator.num_pages)

    template = 'journal/articles.html'
    context = {
        'pinned_articles': pinned_articles,
        'articles': articles,
        'sections': sections,
        'filters': filters,
        'sort': sort,
        'show': show,
        'active_filters': active_filters,
    }
    return render(request, template, context)


@has_journal
def issues(request):
    """ Renders the list of issues in the journal.

    :param request: the request associated with this call
    :return: a rendered template of all issues
    """
    issue_objects = models.Issue.objects.filter(journal=request.journal, issue_type='Issue').order_by("-order")
    template = 'journal/issues.html'
    context = {
        'issues': issue_objects,
    }
    return render(request, template, context)


@has_journal
def current_issue(request, show_sidebar=True):
    """ Renders the current journal issue"""
    return issue(request, request.journal.current_issue_id, show_sidebar=show_sidebar)


@has_journal
def issue(request, issue_id, show_sidebar=True):
    """ Renders a specific issue in the journal.

    :param request: the request associated with this call
    :param issue_id: the ID of the issue to render
    :param show_sidebar: whether or not to show the sidebar of issues
    :return: a rendered template of this issue
    """
    issue_object = get_object_or_404(models.Issue, pk=issue_id, journal=request.journal, issue_type='Issue')
    articles = issue_object.articles.all().order_by('section',
                                                    'page_numbers').prefetch_related('authors', 'frozenauthor_set',
                                                                                     'manuscript_files').select_related(
        'section')

    issue_objects = models.Issue.objects.filter(journal=request.journal, issue_type='Issue')

    template = 'journal/issue.html'
    context = {
        'issue': issue_object,
        'issues': issue_objects,
        'structure': issue_object.structure(),
        'show_sidebar': show_sidebar
    }

    return render(request, template, context)


@has_journal
def collections(request):
    """
    Displays a list of collection Issues.
    :param request: request object
    :return: a rendered template of the collections
    """

    collections = models.Issue.objects.filter(journal=request.journal, issue_type='Collection')

    template = 'journal/collections.html'
    context = {
        'collections': collections,
    }

    return render(request, template, context)


@has_journal
def collection(request, collection_id, show_sidebar=True):
    """
    Displays a single collection.
    :param request: request object
    :param collection_id: primary key of an Issue object
    :param show_sidebar: boolean
    :return: a rendered template
    """

    collection = get_object_or_404(models.Issue, journal=request.journal, issue_type='Collection', pk=collection_id)
    collections = models.Issue.objects.filter(journal=request.journal, issue_type='Collection')

    articles = collection.articles.all().order_by(
        'section', 'page_numbers').prefetch_related('authors', 'manuscript_files').select_related('section')

    template = 'journal/issue.html'
    context = {
        'issue': collection,
        'issues': collections,
        'structure': collection.structure(),
        'show_sidebar': show_sidebar,
        'collection': True,
    }

    return render(request, template, context)


@article_exists
@article_stage_accepted_or_later_required
def article(request, identifier_type, identifier):
    """ Renders an article.

    :param request: the request associated with this call
    :param identifier_type: the identifier type
    :param identifier: the identifier
    :return: a rendered template of the article
    """
    article_object = submission_models.Article.get_article(request.journal, identifier_type, identifier)

    content = None
    galleys = article_object.galley_set.all()

    # check if there is a galley file attached that needs rendering
    if article_object.stage == submission_models.STAGE_PUBLISHED:
        content = list_galleys(article_object, galleys)
    else:
        article_object.abstract = "<p><strong>This is an accepted article with a DOI pre-assigned " \
                                  "that is not yet published.</strong></p>" + article_object.abstract

    if not article_object.large_image_file or article_object.large_image_file.uuid_filename == '':
        article_object.large_image_file = core_models.File()
        # assign the default image with a hacky patch
        # TODO: this should be set to a journal-wide setting
        article_object.large_image_file.uuid_filename = "carousel1.png"
        article_object.large_image_file.is_remote = True

    if article_object.is_published:
        store_article_access(request, article_object, 'view')

    template = 'journal/article.html'
    context = {
        'article': article_object,
        'galleys': galleys,
        'identifier_type': identifier_type,
        'identifier': identifier,
        'article_content': content,
    }

    return render(request, template, context)


@article_exists
@article_stage_accepted_or_later_required
def print_article(request, identifier_type, identifier):
    """ Renders an article.

    :param request: the request associated with this call
    :param identifier_type: the identifier type
    :param identifier: the identifier
    :return: a rendered template of the article
    """
    article_object = submission_models.Article.get_article(request.journal, identifier_type, identifier)

    content = None
    galleys = article_object.galley_set.all()

    # check if there is a galley file attached that needs rendering
    if article_object.stage == submission_models.STAGE_PUBLISHED:
        content = list_galleys(article_object, galleys)
    else:
        article_object.abstract = "This is an accepted article with a DOI pre-assigned that is not yet published."

    if not article_object.large_image_file or article_object.large_image_file.uuid_filename == '':
        article_object.large_image_file = core_models.File()
        # assign the default image with a hacky patch
        # TODO: this should be set to a journal-wide setting
        article_object.large_image_file.uuid_filename = "carousel1.png"
        article_object.large_image_file.is_remote = True

    store_article_access(request, article_object, 'view')

    template = 'journal/print.html'
    context = {
        'article': article_object,
        'galleys': galleys,
        'identifier_type': identifier_type,
        'identifier': identifier,
        'article_content': content
    }

    return render(request, template, context)


@staff_member_required
@has_journal
@article_exists
def edit_article(request, identifier_type, identifier):
    """ Renders the page to edit an article. Note that security enforcement on this view is handled in the submission
    views. All this function does is to redirect to the 'submit_info' view with any identifiers translated to a PK.

    :param request: the request associated with this call
    :param identifier_type: the identifier type
    :param identifier: the identifier
    :return: a rendered template to edit the article
    """
    article_object = submission_models.Article.get_article(request.journal, identifier_type, identifier)

    return redirect(reverse('submit_info', kwargs={'article_id': article_object.pk}))


def download_galley(request, article_id, galley_id):
    """ Serves a galley file for an article

    :param request: an HttpRequest object
    :param article_id: an Article object PK
    :param galley_id: an Galley object PK
    :return: a streaming response of the requested file or a 404.
    """
    article = get_object_or_404(submission_models.Article.allarticles, pk=article_id,
                                stage=submission_models.STAGE_PUBLISHED)
    galley = get_object_or_404(core_models.Galley, pk=galley_id)

    embed = request.GET.get('embed', False)

    if not embed == 'True':
        store_article_access(request, article, 'download', galley_type=galley.file.label)
    return files.serve_file(request, galley.file, article, public=True)


@has_request
@article_stage_accepted_or_later_or_staff_required
@article_exists
@file_user_required
def serve_article_file(request, identifier_type, identifier, file_id):
    """ Serves an article file.

    :param request: the request associated with this call
    :param identifier_type: the identifier type for the article
    :param identifier: the identifier for the article
    :param file_id: the file ID to serve
    :return: a streaming response of the requested file or 404
    """

    article_object = submission_models.Article.get_article(request.journal, identifier_type, identifier)

    try:
        if file_id != "None":
            file_object = get_object_or_404(core_models.File, pk=file_id)
            return files.serve_file(request, file_object, article_object)
        else:
            raise Http404
    except Http404:
        if file_id != "None":
            raise Http404

        # if we are here then the carousel is requesting an image for an article that doesn't exist
        # return a default image instead

        return redirect(static('common/img/default_carousel/carousel1.png'))


@login_required
@article_exists
@file_edit_user_required
def replace_article_file(request, identifier_type, identifier, file_id):
    """ Renders the page to replace an article file

    :param request: the request associated with this call
    :param identifier_type: the identifier type for the article
    :param identifier: the identifier for the article
    :param file_id: the file ID to replace
    :return: a rendered template to replace the file
    """
    article_to_replace = submission_models.Article.get_article(request.journal, identifier_type, identifier)
    file_to_replace = get_object_or_404(core_models.File, pk=file_id)

    error = None

    if request.GET.get('delete', False):
        file_delete(request, article_to_replace.pk, file_to_replace.pk)
        return redirect(reverse('submit_files', kwargs={'article_id': article_to_replace.id}))

    if request.POST:

        if 'replacement' in request.POST:
            uploaded_file = request.FILES.get('replacement-file')
            files.overwrite_file(uploaded_file, article_to_replace, file_to_replace)

        return redirect(request.GET.get('return', 'core_dashboard'))

    template = "journal/replace_file.html"
    context = {
        'article': article_to_replace,
        'old_file': file_to_replace,
        'error': error,
    }

    return render(request, template, context)


@login_required
@article_exists
@file_edit_user_required
def file_reinstate(request, article_id, file_id, file_history_id):
    """ Replaces a file with an older version of itself

    :param request: the request associated with this call
    :param article_id: the article on which to replace the file
    :param file_id: the file ID to replace
    :param file_history_id: the file history object to reinstate
    :return: a redirect to the contents of the GET parameter 'return'
    """
    current_file = get_object_or_404(core_models.File, pk=file_id)
    file_history = get_object_or_404(core_models.FileHistory, pk=file_history_id)

    files.reinstate_historic_file(article, current_file, file_history)

    return redirect(request.GET['return'])


@login_required
@file_edit_user_required
def submit_files_info(request, article_id, file_id):
    """ Renders a template to submit information about a file.

    :param request: the request associated with this call
    :param article_id: the ID of the associated article
    :param file_id: the file ID for which to submit information
    :return: a rendered template to submit file information
    """
    article_object = get_object_or_404(submission_models.Article.allarticlesd, pk=article_id)
    file_object = get_object_or_404(core_models.File, pk=file_id)

    form = review_forms.ReplacementFileDetails(instance=file_object)

    if request.POST:
        form = review_forms.ReplacementFileDetails(request.POST, instance=file_object)
        if form.is_valid():
            form.save()
            # TODO: this needs a better redirect
            return redirect(reverse('kanban'))

    template = "review/submit_replacement_files_info.html"
    context = {
        'article': article_object,
        'file': file_object,
        'form': form,
    }

    return render(request, template, context)


@login_required
@file_history_user_required
def file_history(request, article_id, file_id):
    """ Renders a template to show the history of a file.

    :param request: the request associated with this call
    :param article_id: the ID of the associated article
    :param file_id: the file ID for which to view the history
    :return: a rendered template showing the file history
    """

    if request.POST:
        return redirect(request.GET['return'])

    article_object = get_object_or_404(submission_models.Article.allarticles, pk=article_id)
    file_object = get_object_or_404(core_models.File, pk=file_id)

    template = "journal/file_history.html"
    context = {
        'article': article_object,
        'file': file_object,
    }

    return render(request, template, context)


@login_required
@file_edit_user_required
def file_delete(request, article_id, file_id):
    """ Renders a template to delete a file.

    :param request: the request associated with this call
    :param article_id: the ID of the associated articled
    :param file_id: the file ID for which to view the history
    :return: a redirect to the URL at the GET parameter 'return'
    """
    article_object = get_object_or_404(submission_models.Article.allarticles, pk=article_id)
    file_object = get_object_or_404(core_models.File, pk=file_id)

    file_object.delete()

    return redirect(request.GET['return'])


@file_user_required
@production_user_or_editor_required
def article_file_make_galley(request, article_id, file_id):
    """ Copies a file to be a publicly available galley

    :param request: the request associated with this call
    :param article_id: the ID of the associated articled
    :param file_id: the file ID for which to view the history
    :return: a redirect to the URL at the GET parameter 'return'
    """
    article_object = get_object_or_404(submission_models.Article.allarticles, pk=article_id)
    file_object = get_object_or_404(core_models.File, pk=file_id)

    # we copy the file here so that the user submitting has no control over the typeset files
    # N.B. os.path.splitext[1] always returns the final file extension, even in a multi-dotted (.txt.html etc.) input
    new_filename = str(uuid4()) + str(os.path.splitext(file_object.uuid_filename)[1])
    folder_structure = os.path.join(settings.BASE_DIR, 'files', 'articles', str(article_object.id))

    old_path = os.path.join(folder_structure, str(file_object.uuid_filename))
    new_path = os.path.join(folder_structure, str(new_filename))

    copyfile(old_path, new_path)

    # clone the file model object to a new galley
    new_file = core_models.File(
        mime_type=file_object.mime_type,
        original_filename=file_object.original_filename,
        uuid_filename=new_filename,
        label=file_object.label,
        description=file_object.description,
        owner=request.user,
        is_galley=True
    )

    new_file.save()

    core_models.Galley.objects.create(
        article=article_object,
        file=new_file,
        label=new_file.label,
    )

    return redirect(request.GET['return'])


def identifier_figure(request, identifier_type, identifier, file_name):
    """
    Returns a galley figure from identifier.
    :param request: HttpRequest object
    :param identifier_type: Identifier type string
    :param identifier: An Identifier
    :param file_name: a File object name
    :return: a streaming file reponse
    """
    figure_article = submission_models.Article.get_article(request.journal, identifier_type, identifier)

    if figure_article:
        galley = figure_article.get_render_galley
        figure = get_object_or_404(galley.images, original_filename=file_name)

        return files.serve_file(request, figure, figure_article)
    else:
        raise Http404


def article_figure(request, article_id, galley_id, file_name):
    """ Returns a galley article figure

    :param request: an HttpRequest object
    :param article_id: an Article object PK
    :param galley_id: an Galley object PK
    :param file_name: an File object name
    :return: a streaming file response or a 404 if not found
    """
    figure_article = get_object_or_404(submission_models.Article, pk=article_id)
    galley = get_object_or_404(core_models.Galley, pk=galley_id, article=figure_article)
    figure = get_object_or_404(galley.images, original_filename=file_name)

    return files.serve_file(request, figure, figure_article)


@production_user_or_editor_required
def publish(request):
    """
    Displays a list of articles in pre publication for the current journal
    :param request: django request object
    :return: contextualised django object
    """
    articles = submission_models.Article.objects.filter(stage=submission_models.STAGE_READY_FOR_PUBLICATION,
                                                        journal=request.journal)

    template = 'journal/publish.html'
    context = {
        'articles': articles,
    }

    return render(request, template, context)


@production_user_or_editor_required
def publish_article(request, article_id):
    """
    View allows user to set an article for publication
    :param request: request object
    :param article_id: Article PK
    :return: contextualised django template
    """
    article = get_object_or_404(submission_models.Article,
                                Q(stage=submission_models.STAGE_READY_FOR_PUBLICATION) |
                                Q(stage=submission_models.STAGE_PUBLISHED),
                                pk=article_id,
                                journal=request.journal)
    models.FixedPubCheckItems.objects.get_or_create(article=article)

    doi_data, doi = logic.get_doi_data(article)
    issues = request.journal.issues()
    new_issue_form = issue_forms.NewIssue()
    modal = request.GET.get('m', None)
    pubdate_errors = []

    if request.POST:
        if 'assign_issue' in request.POST:
            logic.handle_assign_issue(request, article, issues)
            return redirect('{0}?m=issue'.format(reverse('publish_article', kwargs={'article_id': article.pk})))

        if 'unassign_issue' in request.POST:
            logic.handle_unassign_issue(request, article, issues)
            return redirect('{0}?m=issue'.format(reverse('publish_article', kwargs={'article_id': article.pk})))

        if 'new_issue' in request.POST:
            new_issue_form, modal, new_issue = logic.handle_new_issue(request)
            if new_issue:
                return redirect('{0}?m=issue'.format(reverse('publish_article', kwargs={'article_id': article.pk})))

        if 'pubdate' in request.POST:
            date_set, pubdate_errors = logic.handle_set_pubdate(request, article)
            if not pubdate_errors:
                return redirect(reverse('publish_article', kwargs={'article_id': article.pk}))
            else:
                modal = 'pubdate'

        if 'author' in request.POST:
            logic.notify_author(request, article)
            return redirect(reverse('publish_article', kwargs={'article_id': article.pk}))

        if 'galley' in request.POST:
            logic.set_render_galley(request, article)
            return redirect(reverse('publish_article', kwargs={'article_id': article.pk}))

        if 'image' in request.POST or 'delete_image' in request.POST:
            logic.set_article_image(request, article)
            shared.clear_cache()
            return redirect("{0}{1}".format(reverse('publish_article', kwargs={'article_id': article.pk}),
                                            "?m=article_image"))

        if 'publish' in request.POST:
            article.stage = submission_models.STAGE_PUBLISHED
            article.snapshot_authors(article)
            article.close_core_workflow_objects()  # TODO: handle plugin elements?

            if not article.date_published:
                article.date_published = timezone.now()

            article.save()

            # Attempt to register xref DOI
            for identifier in article.identifier_set.all():
                if identifier.id_type == 'doi':
                    identifier.register()

            messages.add_message(request, messages.SUCCESS, 'Article set for publication.')

            if request.journal.element_in_workflow(element_name='prepublication'):
                workflow_kwargs = {'handshake_url': 'publish_article',
                                   'request': request,
                                   'article': article,
                                   'switch_stage': True}
                return event_logic.Events.raise_event(event_logic.Events.ON_WORKFLOW_ELEMENT_COMPLETE,
                                                      task_object=article,
                                                      **workflow_kwargs)

        return redirect(reverse('publish_article', kwargs={'article_id': article.pk}))

    template = 'journal/publish_article.html'
    context = {
        'article': article,
        'doi_data': doi_data,
        'doi': doi,
        'issues': issues,
        'new_issue_form': new_issue_form,
        'modal': modal,
        'pubdate_errors': pubdate_errors,
        'notify_author_text': logic.get_notify_author_text(request, article)
    }

    return render(request, template, context)


@require_POST
@production_user_or_editor_required
def publish_article_check(request, article_id):
    """
    A POST only view that updates checklist items on the prepublication page.
    :param request: HttpRequest object
    :param article_id: Artcle object PK
    :return: HttpResponse object
    """
    article = get_object_or_404(submission_models.Article,
                                Q(stage=submission_models.STAGE_READY_FOR_PUBLICATION) |
                                Q(stage=submission_models.STAGE_PUBLISHED),
                                pk=article_id)

    task_type = request.POST.get('task_type')
    id = request.POST.get('id')
    value = True if int(request.POST.get('value')) == 1 else False

    if not task_type or not id:
        return HttpResponse(json.dumps({'error': 'no data supplied'}), content_type="application/json")

    if task_type == 'fixed':
        update_dict = {id: value}
        for k, v in update_dict.items():
            setattr(article.fixedpubcheckitems, k, v)
        article.fixedpubcheckitems.save()

        return HttpResponse(json.dumps({'action': 'ok', 'id': value}), content_type="application/json")

    else:
        item_to_update = get_object_or_404(models.PrePublicationChecklistItem, pk=id, article=article)
        item_to_update.completed = True if int(request.POST.get('value')) == 1 else False
        item_to_update.completed_by = request.user
        item_to_update.completed_on = timezone.now()
        item_to_update.save()
        return HttpResponse(json.dumps({'action': 'ok', 'id': value}), content_type="application/json")


@editor_user_required
def manage_issues(request, issue_id=None, event=None):
    """
    Displays a list of Issue objects, allows them to be sorted and viewed.
    :param request: HttpRequest object
    :param issue_id: Issue object PJ
    :param event: string, 'issue', 'delete' or 'remove'
    :return: HttpResponse object or HttpRedirect if POSTed
    """
    from core.logic import resize_and_crop
    issue_list = models.Issue.objects.filter(journal=request.journal)
    issue, modal, form = None, None, issue_forms.NewIssue()

    if issue_id:
        issue = get_object_or_404(models.Issue, pk=issue_id)
        form = issue_forms.NewIssue(instance=issue)
        if event == 'edit':
            modal = 'issue'
        if event == 'delete':
            modal = 'deleteme'
        if event == 'remove':
            article_id = request.GET.get('article')
            article = get_object_or_404(submission_models.Article, pk=article_id, pk__in=issue.article_pks)
            issue.articles.remove(article)
            return redirect(reverse('manage_issues_id', kwargs={'issue_id': issue.pk}))

    if request.POST:
        if 'make_current' in request.POST:
            issue = models.Issue.objects.get(id=request.POST['make_current'])
            request.journal.current_issue = issue
            request.journal.save()
            issue = None
            return redirect(reverse('manage_issues'))

        if 'delete_issue' in request.POST:
            issue.delete()
            return redirect(reverse('manage_issues'))

        if issue:
            form = issue_forms.NewIssue(request.POST, request.FILES, instance=issue)
        else:
            form = issue_forms.NewIssue(request.POST, request.FILES)

        if form.is_valid():
            save_issue = form.save(commit=False)
            save_issue.journal = request.journal
            save_issue.save()
            if request.FILES and save_issue.large_image:
                resize_and_crop(save_issue.large_image.path, [750, 324])
            if issue:
                return redirect(reverse('manage_issues_id', kwargs={'issue_id': issue.pk}))
            else:
                return redirect(reverse('manage_issues'))
        else:
            modal = 'issue'

    template = 'journal/manage/issues.html'
    context = {
        'issues': issue_list if not issue else [issue],
        'issue': issue,
        'form': form,
        'modal': modal,
    }

    return render(request, template, context)


@editor_user_required
def sort_issue_sections(request, issue_id):
    issue = get_object_or_404(models.Issue, pk=issue_id, journal=request.journal)
    sections = issue.all_sections

    if request.POST:
        if 'up' in request.POST:
            section_id = request.POST.get('up')
            section_to_move_up = get_object_or_404(submission_models.Section, pk=section_id, journal=request.journal)

            if section_to_move_up != issue.first_section:
                section_to_move_up_index = sections.index(section_to_move_up)
                section_to_move_down = sections[section_to_move_up_index - 1]

                section_to_move_up_ordering, c = models.SectionOrdering.objects.get_or_create(
                    issue=issue,
                    section=section_to_move_up)
                section_to_move_down_ordering, c = models.SectionOrdering.objects.get_or_create(
                    issue=issue,
                    section=section_to_move_down)

                section_to_move_up_ordering.order = section_to_move_up_index - 1
                section_to_move_down_ordering.order = section_to_move_up_index

                section_to_move_up_ordering.save()
                section_to_move_down_ordering.save()
            else:
                messages.add_message(request, messages.WARNING, 'You cannot move the first section up the order list')

        elif 'down' in request.POST:
            section_id = request.POST.get('down')
            section_to_move_down = get_object_or_404(submission_models.Section, pk=section_id, journal=request.journal)

            if section_to_move_down != issue.last_section:
                section_to_move_down_index = sections.index(section_to_move_down)
                section_to_move_up = sections[section_to_move_down_index + 1]

                section_to_move_up_ordering, c = models.SectionOrdering.objects.get_or_create(
                    issue=issue,
                    section=section_to_move_up)
                section_to_move_down_ordering, c = models.SectionOrdering.objects.get_or_create(
                    issue=issue,
                    section=section_to_move_down)

                section_to_move_up_ordering.order = section_to_move_down_index
                section_to_move_down_ordering.order = section_to_move_down_index + 1

                section_to_move_up_ordering.save()
                section_to_move_down_ordering.save()

            else:
                messages.add_message(request, messages.WARNING, 'You cannot move the last section down the order list')

    else:
        messages.add_message(request, messages.WARNING, 'This page accepts post requests only.')
    return redirect(reverse('manage_issues_id', kwargs={'issue_id': issue.pk}))


@editor_user_required
def issue_add_article(request, issue_id):
    """
    Allows an editor to add an article to an issue.
    :param request: django request object
    :param issue_id: PK of an Issue object
    :return: a contextualised django template
    """

    issue = get_object_or_404(models.Issue, pk=issue_id, journal=request.journal)
    articles = submission_models.Article.objects.filter(journal=request.journal).exclude(pk__in=issue.article_pks)

    if request.POST.get('article'):
        article_id = request.POST.get('article')
        article = get_object_or_404(submission_models.Article, pk=article_id)
        models.ArticleOrdering.objects.create(article=article, issue=issue, order=issue.next_order())
        issue.articles.add(article)
        return redirect(reverse('manage_issues_id', kwargs={'issue_id': issue.pk}))

    template = 'journal/manage/issue_add_article.html'
    context = {
        'issue': issue,
        'articles': articles,
    }

    return render(request, template, context)


@editor_user_required
def add_guest_editor(request, issue_id):
    """
    Allows an editor to add a guest editor to an issue.
    :param request: django request object
    :param issue_id: PK of an Issue object
    :return: a contextualised django template
    """
    issue = get_object_or_404(models.Issue, pk=issue_id, journal=request.journal)
    users = request.journal.journal_users()
    guest_editors = issue.guest_editors.all()

    if request.POST:
        if 'user' in request.POST:
            user_id = request.POST.get('user')
            user = get_object_or_404(core_models.Account, pk=user_id)

            if user in guest_editors:
                messages.add_message(request, messages.WARNING, 'User is already a guest editor.')
            elif user not in users:
                messages.add_message(request, messages.WARNING, 'This user is not a member of this journal.')
            else:
                issue.guest_editors.add(user)
        elif 'user_remove' in request.POST:
            user_id = request.POST.get('user_remove')
            user = get_object_or_404(core_models.Account, pk=user_id)
            issue.guest_editors.remove(user)

        return redirect(reverse('manage_add_guest_editor', kwargs={'issue_id': issue.pk}))

    template = 'journal/manage/add_guest_editor.html'
    context = {
        'issue': issue,
        'users': users,
        'guest_editors': guest_editors,
    }

    return render(request, template, context)


@csrf_exempt
@editor_user_required
def issue_order(request):
    """
    Takes a list of issue ids and updates their ordering.
    :param request: django request object
    :return: a message
    """

    issues = models.Issue.objects.filter(journal=request.journal)

    if request.POST:
        ids = [int(_id) for _id in request.POST.getlist('issues[]')]

        for issue in issues:
            order = ids.index(issue.pk)
            issue.order = order
            issue.save()

    return HttpResponse('Thanks')


@csrf_exempt
@editor_user_required
def issue_article_order(request, issue_id=None):
    """
    Takes a list if IDs and re-orders an issue's articles.
    :param request: django request object
    :param issue_id: PK of an Issue object
    :return: An ok or error message.
    """

    issue = get_object_or_404(models.Issue, pk=issue_id, journal=request.journal)
    if request.POST:
        ids = request.POST.getlist('articles[]')
        ids = [int(_id) for _id in ids]
        section = get_object_or_404(submission_models.Article, pk=ids[0], journal=request.journal).section

        for article in issue.structure().get(section):
            order = ids.index(article.id)
            article_issue_order, created = models.ArticleOrdering.objects.get_or_create(issue=issue,
                                                                                        article=article,
                                                                                        defaults={'order': order,
                                                                                                  'section': section})
            if not created:
                article_issue_order.order = order
                article_issue_order.section = section
                article_issue_order.save()

    return HttpResponse('Thanks')


@editor_user_required
def manage_archive(request):
    """
    Allows the editor to view information about an article that has been published already.
    :param request: request object
    :return: contextualised django template
    """
    published_articles = submission_models.Article.objects.filter(
        journal=request.journal,
        stage=submission_models.STAGE_PUBLISHED
    ).order_by(
        '-date_published'
    )
    rejected_articles = submission_models.Article.objects.filter(
        journal=request.journal,
        stage=submission_models.STAGE_REJECTED
    ).order_by(
        '-date_declined'
    )

    template = 'journal/manage/archive.html'
    context = {
        'published_articles': published_articles,
        'rejected_articles': rejected_articles,
    }

    return render(request, template, context)


@editor_user_required
def manage_archive_article(request, article_id):
    """
    Allows an editor to edit a previously published article.
    :param request: HttpRequest object
    :param article_id: Article object PK
    :return: HttpResponse or HttpRedirect if Posted
    """
    from production import logic as production_logic
    from identifiers import models as identifier_models
    from submission import forms as submission_forms

    article = get_object_or_404(submission_models.Article, pk=article_id)
    galleys = production_logic.get_all_galleys(article)
    identifiers = identifier_models.Identifier.objects.filter(article=article)

    if request.POST:

        if 'xml' in request.POST:
            for uploaded_file in request.FILES.getlist('xml-file'):
                production_logic.save_galley(article, request, uploaded_file, True, "XML", False)

        if 'pdf' in request.POST:
            for uploaded_file in request.FILES.getlist('pdf-file'):
                production_logic.save_galley(article, request, uploaded_file, True, "PDF", False)

        if 'delete_note' in request.POST:
            note_id = int(request.POST['delete_note'])
            publisher_note = submission_models.PublisherNote.objects.get(pk=note_id)
            publisher_note.delete()

        if 'add_publisher_note' in request.POST:
            pn = submission_models.PublisherNote()
            pn.creator = request.user
            pn.sequence = 0
            pn_form = submission_forms.PublisherNoteForm(data=request.POST, instance=pn)
            pn_form.save()

            article.publisher_notes.add(pn)
            article.save()

        if 'save_publisher_note' in request.POST:
            note_id = int(request.POST['save_publisher_note'])
            pn = submission_models.PublisherNote.objects.get(pk=note_id)
            pn_form = submission_forms.PublisherNoteForm(data=request.POST, instance=pn)
            pn_form.save()

        if 'other' in request.POST:
            for uploaded_file in request.FILES.getlist('other-file'):
                production_logic.save_galley(article, request, uploaded_file, True, "Other", True)

        return redirect(reverse('manage_archive_article', kwargs={'article_id': article.pk}))

    newnote_form = submission_forms.PublisherNoteForm()

    note_forms = []

    for publisher_note in article.publisher_notes.all():
        note_form = submission_forms.PublisherNoteForm(instance=publisher_note)
        note_forms.append(note_form)

    template = 'journal/manage/archive_article.html'
    context = {
        'article': article,
        'galleys': galleys,
        'identifiers': identifiers,
        'newnote_form': newnote_form,
        'note_forms': note_forms
    }

    return render(request, template, context)


@editor_user_required
def publication_schedule(request):
    """
    Displays a list of articles that have been set for publication but are not yet published.
    :param request: HttpRequest object
    :return: HttpReponse
    """
    article_list = submission_models.Article.objects.filter(journal=request.journal,
                                                            date_published__gte=timezone.now())

    template = 'journal/manage/publication_schedule.html'
    context = {
        'articles': article_list,
    }

    return render(request, template, context)


@login_required
def become_reviewer(request):
    """
    If a user is signed in and not a reviewer, lets them become one, otherwsie asks them to login/tells them they
    are already a reviewer
    :param request: django request object
    :return: a contextualised django template
    """

    # The user needs to login before we can do anything else
    code = 'not-logged-in'
    message = _('You must login before you can become a reviewer. Click the button below to login.')

    if request.user and request.user.is_authenticated() and not request.user.is_reviewer(request):
        # We have a user, they are logged in and not yet a reviewer
        code = 'not-reviewer'
        message = _('You are not yet a reviewer for this journal. Click the button below to become a reviewer.')

    elif request.user and request.user.is_authenticated() and request.user.is_reviewer(request):
        # The user is logged in, and is already a reviewer
        code = 'already-reviewer'
        message = _('You are already a reviewer.')

    if request.POST.get('action', None) == 'go':
        request.user.add_account_role('reviewer', request.journal)
        messages.add_message(request, messages.SUCCESS, _('You are now a reviewer'))
        return redirect(reverse('core_dashboard'))

    template = 'journal/become_reviewer.html'
    context = {
        'code': code,
        'message': message,
    }

    return render(request, template, context)


def contact(request):
    """
    Displays a form that allows a user to contact admins or editors.
    :param request: HttpRequest object
    :return: HttpResponse or HttpRedirect if POST
    """
    subject = request.GET.get('subject', '')
    contacts = core_models.Contacts.objects.filter(content_type=request.model_content_type,
                                                   object_id=request.site_type.pk)

    contact_form = forms.ContactForm(subject=subject, contacts=contacts)

    if request.POST:
        contact_form = forms.ContactForm(request.POST, contacts=contacts)

        if contact_form.is_valid():
            new_contact = contact_form.save(commit=False)
            new_contact.client_ip = shared.get_ip_address(request)
            new_contact.content_type = request.model_content_type
            new_contact.object_ic = request.site_type.pk
            new_contact.save()

            logic.send_contact_message(new_contact, request)
            messages.add_message(request, messages.SUCCESS, 'Your message has been sent.')
            return redirect(reverse('contact'))

    template = 'journal/contact.html'
    context = {
        'contact_form': contact_form,
        'contacts': contacts,
    }

    return render(request, template, context)


def editorial_team(request, group_id=None):
    """
    Displays a list of Editorial team members, an optional ID can be supplied to limit the display to a group only.
    :param request: HttpRequest object
    :param group_id: EditorailGroup object PK
    :return: HttpResponse object
    """
    if group_id:
        editorial_groups = core_models.EditorialGroup.objects.filter(journal=request.journal, pk=group_id)
    else:
        editorial_groups = core_models.EditorialGroup.objects.filter(journal=request.journal)

    template = 'journal/editorial_team.html'
    context = {
        'editorial_groups': editorial_groups,
        'group_id': group_id,
    }

    return render(request, template, context)


def sitemap(request):
    """
    Renders an XML sitemap based on articles and pages available to the journal.
    :param request: HttpRequest object
    :return: HttpResponse object
    """
    articles = submission_models.Article.objects.filter(date_published__lte=timezone.now(), journal=request.journal)
    cms_pages = cms_models.Page.objects.filter(object_id=request.site_type.id, content_type=request.model_content_type)

    template = 'journal/sitemap.xml'

    context = {
        'articles': articles,
        'cms_pages': cms_pages,
    }
    return render(request, template, context, content_type="application/xml")


def search(request):
    """
    Allows a user to search for articles by name or author name.
    :param request: HttpRequest object
    :return: HttpResponse object
    """
    articles = []
    search_term = None

    if request.POST:
        search_term = request.POST.get('search')
        request.session['article_search'] = search_term
        return redirect(reverse('search'))

    if request.session.get('article_search'):
        search_term = request.session.get('article_search')

        article_search = submission_models.Article.objects.filter(
            (Q(title__icontains=search_term) |
             Q(subtitle__icontains=search_term)) &
            Q(journal=request.journal)
        )
        article_search = [article for article in article_search]

        author_search = search_term.split(' ')
        from_author = submission_models.FrozenAuthor.objects.filter(
            (Q(first_name__in=author_search) |
             Q(last_name__in=author_search)) &
            Q(article__journal=request.journal)
        )

        articles_from_author = [author.article for author in from_author]
        articles = set(article_search + articles_from_author)

    template = 'journal/search.html'
    context = {
        'articles': articles,
        'search_term': search_term
    }

    return render(request, template, context)


def submissions(request):
    """
    Displays a submission information page with info on sections and licenses etc.
    :param request: HttpRequest object
    :return: HttpResponse object
    """
    template = 'journal/submissions.html'
    context = {
        'sections': submission_models.Section.objects.language().fallbacks('en').filter(journal=request.journal,
                                                                                        public_submissions=True),
        'licenses': submission_models.Licence.objects.filter(journal=request.journal, available_for_submission=True)
    }

    return render(request, template, context)


@editor_user_required
def manage_article_log(request, article_id):
    """
    Displays a list of article log items.
    :param request: HttpRequest object
    :param article_id: Article object PK
    :return: HttpResponse object
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    content_type = ContentType.objects.get_for_model(article)
    log_entries = utils_models.LogEntry.objects.filter(content_type=content_type, object_id=article.pk)

    if request.POST and settings.ENABLE_ENHANCED_MAILGUN_FEATURES:
        call_command('check_mailgun_stat')
        return redirect(reverse('manage_article_log', kwargs={'article_id': article.pk}))

    template = 'journal/article_log.html'
    context = {
        'article': article,
        'log_entries': log_entries,
        'return': request.GET.get('return', None)
    }

    return render(request, template, context)


@editor_user_required
def resend_logged_email(request, article_id, log_id):
    article = get_object_or_404(submission_models.Article, pk=article_id)
    log_entry = get_object_or_404(utils_models.LogEntry, pk=log_id)
    form = forms.ResendEmailForm(log_entry=log_entry)
    close = False

    if request.POST and 'resend' in request.POST:
        form = forms.ResendEmailForm(request.POST, log_entry=log_entry)

        if form.is_valid():
            logic.resend_email(article, log_entry, request, form)
            close = True

    template = 'journal/resend_logged_email.html'
    context = {
        'article': article,
        'log_entry': log_entry,
        'form': form,
        'close': close,
    }

    return render(request, template, context)


@editor_user_required
def new_note(request, article_id):
    """
    Generates a new Note object, must be POST.
    :param request: HttpRequest object
    :param article_id: Article object PK
    :return: HttpResponse object
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)

    if request.POST:

        note = request.POST.get('note')

        sav_note = submission_models.Note.objects.create(
            article=article,
            creator=request.user,
            text=note,
        )

        return_dict = {'id': sav_note.pk, 'note': sav_note.text, 'initials': sav_note.creator.initials(),
                       'date_time': str(sav_note.date_time),
                       'html': logic.create_html_snippet(sav_note)}

    else:

        return_dict = {'error': 'This request must be made with POST'}

    return HttpResponse(json.dumps(return_dict), content_type="application/json")


@editor_user_required
def delete_note(request, article_id, note_id):
    """
    Deletes a Note object.
    :param request: HttpRequest object
    :param article_id: Article object PK
    :param note_id: Note object PK
    :return: HttpResponse
    """
    note = get_object_or_404(submission_models.Note, pk=note_id)
    note.delete()

    return HttpResponse


def download_journal_file(request, file_id):
    file = get_object_or_404(core_models.File, pk=file_id)

    if file.privacy == 'public' or (request.user.is_authenticated() and request.user.is_staff) or \
            (request.user.is_authenticated() and request.user.is_editor(request)):
        return files.serve_journal_cover(request, file)
    else:
        raise Http404


def download_table(request, identifier_type, identifier, table_name):
    """
    For an JATS xml document, renders it into HTML and pulls a CSV from there.
    :param request: HttpRequest
    :param identifier_type: Article Identifier type eg. id or doi
    :param identifier: Article Identifier eg. 123 or 10.1167/1234
    :param table_name: The ID of the table inside the HTML
    :return: StreamingHTTPResponse with CSV attached
    """
    article = submission_models.Article.get_article(request.journal, identifier_type, identifier)
    galley = article.get_render_galley

    if galley.file.mime_type.endswith('/xml'):
        content = galley.file_content()
        table = logic.get_table_from_html(table_name, content)
        csv = logic.parse_html_table_to_csv(table, table_name)
        return files.serve_temp_file(csv, '{0}.csv'.format(table_name))


def download_supp_file(request, article_id, supp_file_id):
    article = get_object_or_404(submission_models.Article.allarticles, pk=article_id,
                                stage=submission_models.STAGE_PUBLISHED)
    supp_file = get_object_or_404(core_models.SupplementaryFile, pk=supp_file_id)

    return files.serve_file(request, supp_file.file, article, public=True)


@staff_member_required
def texture_edit(request, file_id):
    file = get_object_or_404(core_models.File, pk=file_id)

    template = 'admin/journal/texture.html'
    context = {
        'file': file,
        'content': files.get_file(file, file.article).replace('\n', '')
    }

    return render(request, template, context)


@editor_user_required
def document_management(request, article_id):
    document_article = get_object_or_404(submission_models.Article, pk=article_id)
    article_files = core_models.File.objects.filter(article_id=document_article.pk)
    return_url = request.GET.get('return', '/dashboard/')

    if request.POST and request.FILES:

        if 'manu' in request.POST:
            from core import files as core_files
            file = request.FILES.get('manu-file')
            new_file = core_files.save_file_to_article(file, document_article,
                                                       request.user, label='MS File', is_galley=False)
            document_article.manuscript_files.add(new_file)
            messages.add_message(request, messages.SUCCESS, 'Production file uploaded.')

        if 'prod' in request.POST:
            from production import logic as prod_logic
            file = request.FILES.get('prod-file')
            prod_logic.save_prod_file(document_article, request, file, 'Production Ready File')
            messages.add_message(request, messages.SUCCESS, 'Production file uploaded.')

        if 'proof' in request.POST:
            from production import logic as prod_logic
            file = request.FILES.get('proof-file')
            prod_logic.save_galley(document_article, request, file, True, 'File for Proofing', is_other=False)
            messages.add_message(request, messages.SUCCESS, 'Proofing file uploaded.')

        return redirect(reverse('document_management', kwargs={'article_id': document_article.pk}))

    template = 'admin/journal/document_management.html'
    context = {
        'files': article_files,
        'article': document_article,
        'return_url': return_url,
    }

    return render(request, template, context)
