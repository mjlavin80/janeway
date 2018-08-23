__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"


from uuid import uuid4

from django.contrib import messages
from django.urls import reverse
from django.db import IntegrityError
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.utils import timezone
from django.http import Http404
from django.contrib.admin.views.decorators import staff_member_required
from django.conf import settings

from core import models as core_models, files, forms as core_forms
from events import logic as event_logic
from review import models, logic, forms, hypothesis
from security.decorators import editor_user_required, reviewer_user_required, reviewer_user_for_assignment_required, \
    article_edit_user_required, file_user_required, article_decision_not_made, article_author_required, \
    editor_is_not_author, senior_editor_user_required, section_editor_draft_decisions
from submission import models as submission_models
from utils import models as util_models, ithenticate


@senior_editor_user_required
def home(request):
    """
    Displays a list of review articles.
    :param request: HttpRequest object
    :return: HttpResponse
    """
    articles = submission_models.Article.objects.filter(
        Q(stage=submission_models.STAGE_ASSIGNED) |
        Q(stage=submission_models.STAGE_UNDER_REVIEW) |
        Q(stage=submission_models.STAGE_UNDER_REVISION),
        journal=request.journal
    )

    filter = request.GET.get('filter', None)
    if filter == 'me':
        assignments = models.EditorAssignment.objects.filter(article__journal=request.journal,
                                                             editor=request.user)
        assignment_article_pks = [assignment.article.pk for assignment in assignments]
        articles = articles.filter(pk__in=assignment_article_pks)

    template = 'review/home.html'
    context = {
        'articles': articles,
        'filter': filter,
    }

    return render(request, template, context)


@senior_editor_user_required
def unassigned(request):
    """
    Displays a list of unassigned articles.
    :param request: HttpRequest object
    :return: HttpResponse
    """
    articles = submission_models.Article.objects.filter(stage=submission_models.STAGE_UNASSIGNED,
                                                        journal=request.journal)

    template = 'review/unassigned.html'
    context = {
        'articles': articles,
    }

    return render(request, template, context)


@senior_editor_user_required
def unassigned_article(request, article_id):
    """
    Displays metadata of an individual article, can send details to Crosscheck for reporting.
    :param request: HttpRequest object
    :param article_id: Article PK
    :return: HttpResponse or Redirect if POST
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)

    if article.ithenticate_id and not article.ithenticate_score:
        ithenticate.fetch_percentage(request.journal, [article])

    if 'crosscheck' in request.POST:
        file_id = request.POST.get('crosscheck')
        file = get_object_or_404(core_models.File, pk=file_id)
        id = ithenticate.send_to_ithenticate(article, file)
        article.ithenticate_id = id
        article.save()
        return redirect(reverse('review_unassigned_article', kwargs={'article_id': article.pk}))

    current_editors = [assignment.editor.pk for assignment in models.EditorAssignment.objects.filter(article=article)]
    editors = core_models.AccountRole.objects.filter(role__slug='editor',
                                                     journal=request.journal).exclude(user__id__in=current_editors)
    section_editors = core_models.AccountRole.objects.filter(role__slug='section-editor',
                                                             journal=request.journal
                                                             ).exclude(user__id__in=current_editors)

    template = 'review/unassigned_article.html'
    context = {
        'article': article,
        'editors': editors,
        'section_editors': section_editors,
    }

    return render(request, template, context)


@senior_editor_user_required
def view_ithenticate_report(request, article_id):
    """Allows editor to view similarity report."""
    article = get_object_or_404(submission_models.Article, pk=article_id, ithenticate_id__isnull=False)

    ithenticate_url = ithenticate.fetch_url(article)

    if ithenticate_url:
        return redirect(ithenticate_url)

    template = 'review/ithenticate_failure.html'
    context = {
        'article': article,
    }

    return render(request, template, context)


@senior_editor_user_required
def assign_editor_move_to_review(request, article_id, editor_id, assignment_type):
    """Allows an editor to assign another editor to an article and moves to review."""
    assign_editor(request, article_id, editor_id, assignment_type, should_redirect=False)
    return move_to_review(request, article_id)


@senior_editor_user_required
def assign_editor(request, article_id, editor_id, assignment_type, should_redirect=True):
    """
    Allows a Senior Editor to assign another editor to an article.
    :param request: HttpRequest object
    :param article_id: Article PK
    :param editor_id: Account PK
    :param assignment_type: string, 'section-editor' or 'editor'
    :param should_redirect: if true, we redirect the user to the notification page
    :return: HttpResponse or HttpRedirect
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    editor = get_object_or_404(core_models.Account, pk=editor_id)

    if not editor.has_an_editor_role(request):
        messages.add_message(request, messages.WARNING, 'User is not an Editor or Section Editor')
        return redirect(reverse('review_unassigned_article', kwargs={'article_id': article.pk}))

    try:
        assignment = models.EditorAssignment.objects.create(article=article, editor=editor, editor_type=assignment_type)
        messages.add_message(request, messages.SUCCESS, '{0} added as an Editor'.format(editor.full_name()))

        kwargs = {'user_message_content': '',
                  'editor_assignment': assignment,
                  'request': request,
                  'skip': True,
                  'acknowledgement': False}

        event_logic.Events.raise_event(event_logic.Events.ON_ARTICLE_ASSIGNED, task_object=article, **kwargs)

        if should_redirect:
            return redirect('{0}?return={1}'.format(
                reverse('review_assignment_notification', kwargs={'article_id': article_id, 'editor_id': editor.pk}),
                request.GET.get('return')))
    except IntegrityError:
        messages.add_message(request, messages.WARNING,
                             '{0} is already an Editor on this article.'.format(editor.full_name()))
        if should_redirect:
            return redirect(reverse('review_unassigned_article', kwargs={'article_id': article_id}))


@senior_editor_user_required
def unassign_editor(request, article_id, editor_id):
    """Unassigns an editor from an article"""
    article = get_object_or_404(submission_models.Article, pk=article_id)
    editor = get_object_or_404(core_models.Account, pk=editor_id)
    assignment = get_object_or_404(models.EditorAssignment, article=article, editor=editor)

    assignment.delete()

    util_models.LogEntry.add_entry(types='EditorialAction',
                                   description='{0} unassigned from article {1}'.format(editor.full_name(), article.id),
                                   level='Info',
                                   request=request, target=article)

    return redirect(reverse('review_unassigned_article', kwargs={'article_id': article_id}))


@senior_editor_user_required
def assignment_notification(request, article_id, editor_id):
    """
    A senior editor can sent a notification to an assigned editor.
    :param request: HttpRequest object
    :param article_id: Article PK
    :param editor_id: Account PK
    :return: HttpResponse or HttpRedirect
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    editor = get_object_or_404(core_models.Account, pk=editor_id)
    assignment = get_object_or_404(models.EditorAssignment, article=article, editor=editor, notified=False)

    email_content = logic.get_assignment_content(request, article, editor, assignment)

    if request.POST:

        email_content = request.POST.get('content_email')
        kwargs = {'user_message_content': email_content,
                  'editor_assignment': assignment,
                  'request': request,
                  'skip': False,
                  'acknowledgement': True}

        if 'skip' in request.POST:
            kwargs['skip'] = True

        event_logic.Events.raise_event(event_logic.Events.ON_ARTICLE_ASSIGNED_ACKNOWLEDGE, **kwargs)

        if request.GET.get('return', None):
            return redirect(request.GET.get('return'))
        else:
            return redirect(reverse('review_unassigned_article', kwargs={'article_id': article_id}))

    template = 'review/assignment_notification.html'
    context = {
        'article': article_id,
        'editor': editor,
        'assignment': assignment,
        'email_content': email_content,
    }

    return render(request, template, context)


@editor_user_required
def move_to_review(request, article_id, should_redirect=True):
    """Moves an article into the review stage"""
    article = get_object_or_404(submission_models.Article, pk=article_id)

    if article.editorassignment_set.all().count() > 0:
        article.stage = submission_models.STAGE_ASSIGNED
        article.save()
        review_round, created = models.ReviewRound.objects.get_or_create(article=article, round_number=1)

        if not created:
            messages.add_message(request, messages.WARNING, 'A default review round already exists for this article.')

    else:
        messages.add_message(request, messages.INFO, 'You must assign an editor before moving into reivew.')

    if should_redirect:
        if request.GET.get('return', None):
            return redirect(request.GET.get('return'))
        else:
            return redirect("{0}?modal_id={1}".format(reverse('kanban_home'), article_id))


@editor_is_not_author
@editor_user_required
def in_review(request, article_id):
    """
    Displays an article's review management page
    :param request: HttpRequest object
    :param article_id: Article PK
    :return: HttpResponse
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review_rounds = models.ReviewRound.objects.filter(article=article)
    revisions_requests = models.RevisionRequest.objects.filter(article=article)

    if not review_rounds:
        models.ReviewRound.objects.create(article=article, round_number=1)
        return redirect(reverse('review_in_review', kwargs={'article_id': article.id}))

    if request.POST:

        if 'new_review_round' in request.POST:

            # Complete all existing review assignments.
            for assignment in article.current_review_round_object().reviewassignment_set.all():
                if not assignment.date_complete:
                    assignment.date_complete = timezone.now()
                    assignment.decision = 'withdrawn'
                    assignment.is_complete = True
                    assignment.save()
                    messages.add_message(request, messages.INFO, 'Assignment {0} closed.'.format(assignment.id))

                kwargs = {'review_assignment': assignment,
                          'request': request}
                event_logic.Events.raise_event(event_logic.Events.ON_REVIEW_CLOSED,
                                               task_object=assignment.article,
                                               **kwargs)

            # Add a new review round.
            new_round_number = article.current_review_round() + 1
            models.ReviewRound.objects.create(article=article, round_number=new_round_number)

            article.stage = submission_models.STAGE_UNDER_REVIEW
            article.save()

        return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))

    template = 'review/in_review.html'
    context = {
        'article': article,
        'review_rounds': review_rounds,
        'revisions_requests': revisions_requests,
    }

    return render(request, template, context)


@editor_is_not_author
@editor_user_required
def delete_review_round(request, article_id, round_id):
    """
    Deletes a review round if it is not already closed.
    :param request: HttpRequest object
    :param article_id: Article PK
    :param round_id: Round PK
    :return: HttpResponse or HttpRedirect
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review_round = get_object_or_404(models.ReviewRound, pk=round_id)

    if request.POST:
        if 'delete' in request.POST:
            review_round.delete()

            if article.is_under_revision():
                article.stage = submission_models.STAGE_UNDER_REVISION
                article.save()

            messages.add_message(request, messages.INFO, 'Round {0} deleted.'.format(review_round.round_number))
            return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))

    elif not review_round.round_number == article.current_review_round():
        messages.add_message(request, messages.INFO, 'Cannot delete a closed round.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))

    template = 'review/delete_review_round.html'
    context = {
        'article': article,
        'round': review_round,
    }

    return render(request, template, context)


@editor_is_not_author
@article_decision_not_made
@editor_user_required
def add_files(request, article_id, round_id):
    """
    Interface for adding files to a review round.
    :param request: HttpRequest object
    :param article_id: Article PK
    :param round_id: Round PK
    :return: HttpResponse or HttpRedirect
    """
    article = get_object_or_404(submission_models.Article.objects.prefetch_related('manuscript_files'), pk=article_id)
    review_round = get_object_or_404(models.ReviewRound.objects.prefetch_related('review_files'), pk=round_id)

    if request.POST:

        if 'upload' in request.POST:
            review_files = request.FILES.getlist('review_file')

            if review_files:
                for review_file in review_files:
                    new_file_obj = files.save_file_to_article(review_file, article, request.user, 'Review File')
                    article.manuscript_files.add(new_file_obj)
                messages.add_message(request, messages.SUCCESS, 'File uploaded')
            else:
                messages.add_message(request, messages.WARNING, 'No file uploaded.')

            return redirect(reverse('review_add_files', kwargs={'article_id': article.pk, 'round_id': review_round.pk}))

        for file in request.POST.getlist('file'):
            file = core_models.File.objects.get(id=file)
            review_round.review_files.add(file)
            messages.add_message(request, messages.INFO, 'File {0} added.'.format(file.label))

        if not request.POST.getlist('file'):
            messages.add_message(request, messages.WARNING,
                                 'Please select at least one file, or press the Cancel button.')

        return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))

    template = 'review/add_files.html'
    context = {
        'article': article,
        'round': review_round,
    }

    return render(request, template, context)


@editor_is_not_author
@article_decision_not_made
@editor_user_required
def remove_file(request, article_id, round_id, file_id):
    """Removes a file from a review round."""
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review_round = get_object_or_404(models.ReviewRound, pk=round_id)
    file = get_object_or_404(core_models.File, pk=file_id)

    if review_round.round_number == article.current_review_round():
        review_round.review_files.remove(file)
        messages.add_message(request, messages.INFO, 'File {0} removed.'.format(file.label))
    else:
        messages.add_message(request, messages.INFO,
                             'Cannot remove a file from a closed review round.'.format(file.label))
    return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))


@reviewer_user_for_assignment_required
def accept_review_request(request, assignment_id):
    """
    Accept a review request
    :param request: the request object
    :param assignment_id: the assignment ID to handle
    :return: a context for a Django template
    """

    access_code = logic.get_access_code(request)

    # update the ReviewAssignment object
    if access_code:
        assignment = models.ReviewAssignment.objects.get(Q(pk=assignment_id) &
                                                         Q(is_complete=False) &
                                                         Q(access_code=access_code) &
                                                         Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
                                                         Q(date_accepted__isnull=True))
    else:
        assignment = models.ReviewAssignment.objects.get(Q(pk=assignment_id) &
                                                         Q(is_complete=False) &
                                                         Q(reviewer=request.user) &
                                                         Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
                                                         Q(date_accepted__isnull=True))

    assignment.date_accepted = timezone.now()
    assignment.save()

    kwargs = {'review_assignment': assignment,
              'request': request,
              'accepted': True}
    event_logic.Events.raise_event(event_logic.Events.ON_REVIEWER_ACCEPTED,
                                   task_object=assignment.article,
                                   **kwargs)

    return redirect(logic.generate_access_code_url('do_review', assignment, access_code))


@reviewer_user_for_assignment_required
def decline_review_request(request, assignment_id):
    """
    Decline a review request
    :param request: the request object
    :param assignment_id: the assignment ID to handle
    :return: a context for a Django template
    """

    access_code = logic.get_access_code(request)

    if access_code:
        assignment = models.ReviewAssignment.objects.get(
            Q(pk=assignment_id) &
            Q(is_complete=False) &
            Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
            Q(access_code=access_code)
        )
    else:
        assignment = models.ReviewAssignment.objects.get(
            Q(pk=assignment_id) &
            Q(is_complete=False) &
            Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
            Q(reviewer=request.user)
        )

    assignment.date_declined = timezone.now()
    assignment.date_accepted = None
    assignment.is_complete = True
    assignment.save()

    template = 'review/review_decline.html'
    context = {
        'assigned_articles_for_user_review': assignment,
        'access_code': access_code if access_code else ''
    }

    kwargs = {'review_assignment': assignment,
              'request': request,
              'accepted': False}
    event_logic.Events.raise_event(event_logic.Events.ON_REVIEWER_DECLINED,
                                   task_object=assignment.article,
                                   **kwargs)

    return render(request, template, context)


@reviewer_user_for_assignment_required
def suggest_reviewers(request, assignment_id):
    """
    Allows a user to suggest reviewers
    :param request:
    :param assignment_id:
    :return:
    """
    try:
        access_code = logic.get_access_code(request)

        if access_code:
            assignment = models.ReviewAssignment.objects.get(
                Q(pk=assignment_id) &
                Q(is_complete=True) &
                Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
                Q(access_code=access_code)
            )
        else:
            assignment = models.ReviewAssignment.objects.get(
                Q(pk=assignment_id) &
                Q(is_complete=True) &
                Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
                Q(reviewer=request.user)
            )
    except models.ReviewAssignment.DoesNotExist:
        raise PermissionError('Suggested reviewers already supplied.')

    form = forms.SuggestReviewers(instance=assignment)
    if request.POST:
        form = forms.SuggestReviewers(request.POST, instance=assignment)

        if form.is_valid():
            form.save()

            messages.add_message(request, messages.INFO, 'Thanks for suggesting reviewers for this article.')
            return redirect(reverse('website_index'))

    template = 'review/suggest_reviewers.html'
    context = {
        'assignment': assignment,
        'form': form,
    }

    return render(request, template, context)


@reviewer_user_required
def review_requests(request):
    """
    A list of requests for the current user
    :param request: the request object
    :return: a context for a Django template
    """
    new_requests = models.ReviewAssignment.objects.filter(
        Q(is_complete=False) &
        Q(reviewer=request.user) &
        Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
        Q(date_accepted__isnull=True),
        article__journal=request.journal
    )

    active_requests = models.ReviewAssignment.objects.filter(
        Q(is_complete=False) &
        Q(reviewer=request.user) &
        Q(article__stage=submission_models.STAGE_UNDER_REVIEW),
        Q(date_accepted__isnull=False),
        article__journal=request.journal
    )

    completed_requests = models.ReviewAssignment.objects.filter(
        Q(is_complete=True) &
        Q(reviewer=request.user),
        article__journal=request.journal
    )

    template = 'review/review_requests.html'
    context = {
        'new_requests': new_requests,
        'active_requests': active_requests,
        'completed_requests': completed_requests,
    }

    return render(request, template, context)


@reviewer_user_for_assignment_required
def do_review(request, assignment_id):
    """
    Rendering of the review form for user to complete.
    :param request: the request object
    :param assignment_id: ReviewAssignment PK
    :return: a context for a Django template
    """

    access_code = logic.get_access_code(request)

    if access_code:
        assignment = models.ReviewAssignment.objects.get(
            Q(pk=assignment_id) &
            Q(is_complete=False) &
            Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
            Q(access_code=access_code)
        )
    else:
        assignment = models.ReviewAssignment.objects.get(
            Q(pk=assignment_id) &
            Q(is_complete=False) &
            Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
            Q(reviewer=request.user)
        )

    fields_required = True
    review_round = assignment.article.current_review_round_object()
    form = forms.GeneratedForm(review_assignment=assignment)
    decision_form = forms.ReviewerDecisionForm(instance=assignment)

    if 'review_file' in request.GET:
        return logic.serve_review_file(assignment)

    if request.POST:
        if 'decline' in request.POST:
            return redirect(logic.generate_access_code_url('decline_review', assignment, access_code))

        if 'accept' in request.POST:
            return redirect(logic.generate_access_code_url('accept_review', assignment, access_code))

        if request.FILES:
            fields_required = False
            uploaded_file = request.FILES.get('review_file')
            new_file = files.save_file_to_article(uploaded_file, assignment.article, assignment.reviewer)
            assignment.review_file = new_file
            assignment.save()

        form = forms.GeneratedForm(request.POST, review_assignment=assignment, fields_required=fields_required)
        decision_form = forms.ReviewerDecisionForm(request.POST, instance=assignment)

        if form.is_valid() and decision_form.is_valid():
            decision_form.save()
            assignment.save_review_form(form, assignment)
            assignment.date_complete = timezone.now()
            assignment.is_complete = True

            if not assignment.date_accepted:
                assignment.date_accepted = timezone.now()

            assignment.save()

            kwargs = {'review_assignment': assignment,
                      'request': request}
            event_logic.Events.raise_event(event_logic.Events.ON_REVIEW_COMPLETE,
                                           task_object=assignment.article,
                                           **kwargs)

            return redirect(logic.generate_access_code_url('thanks_review', assignment, access_code))

    template = 'review/review_form.html'
    context = {
        'assignment': assignment,
        'form': form,
        'decision_form': decision_form,
        'review_round': review_round,
        'access_code': access_code,
    }

    return render(request, template, context)


@reviewer_user_for_assignment_required
def thanks_review(request, assignment_id):
    """
    Displays thank you message for the assignment form.
    :param request: HttpRequest object
    :param assignment_id: ReviewAssignment PK
    :return: HttpResponse
    """
    access_code = logic.get_access_code(request)

    if access_code:
        assignment = models.ReviewAssignment.objects.get(
            Q(pk=assignment_id) &
            Q(is_complete=True) &
            Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
            Q(access_code=access_code)
        )
    else:
        assignment = models.ReviewAssignment.objects.get(
            Q(pk=assignment_id) &
            Q(is_complete=True) &
            Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
            Q(reviewer=request.user)
        )

    template = 'review/thanks.html'
    context = {
        'assignment': assignment,
        'access_code': access_code,
    }

    return render(request, template, context)


@editor_is_not_author
@article_decision_not_made
@editor_user_required
def add_review_assignment(request, article_id):
    """
    Allow an editor to add a new review assignment
    :param request: HttpRequest object
    :param article_id: Article PK
    :return: HttpResponse
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    form = forms.ReviewAssignmentForm(journal=request.journal)
    new_reviewer_form = core_forms.QuickUserForm()
    reviewers = logic.get_reviewers(article, request)
    suggested_reviewers = logic.get_suggested_reviewers(article, reviewers)
    user_list = logic.get_enrollable_users(request)

    modal = None

    # Check if this review round has files
    if not article.current_review_round_object().review_files.all():
        messages.add_message(request, messages.WARNING, 'You should select files for review before adding reviwers.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    if request.POST:

        if 'quick_assign' in request.POST:
            logic.quick_assign(request, article)
            return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))
        elif 'assign' in request.POST:
            new_reviewer_form = core_forms.QuickUserForm(request.POST)
            if new_reviewer_form.is_valid():
                logic.handle_reviewer_form(request, new_reviewer_form)
                return redirect(reverse('review_add_review_assignment', kwargs={'article_id': article.pk}))
            else:
                modal = 'reviewer'
        elif 'enrollusers' in request.POST:
            user_ids = request.POST.getlist('user_id')
            users = core_models.Account.objects.filter(pk__in=user_ids)
            for user in users:
                user.add_account_role('reviewer', request.journal)
                messages.add_message(request, messages.SUCCESS, '{0} enrolled as a reviewer.'.format(user.full_name()))
            return redirect(reverse('review_add_review_assignment', kwargs={'article_id': article.pk}))
        else:

            form = forms.ReviewAssignmentForm(request.POST, journal=request.journal)

            if form.is_valid():
                reviewer = logic.get_reviewer_from_post(request)

                if not reviewer:
                    form.add_error(None, 'You must select a reviewer.')
                else:
                    review_assignment = form.save(commit=False)
                    review_assignment.reviewer = reviewer
                    review_assignment.article = article
                    review_assignment.editor = request.user
                    review_assignment.review_round = article.current_review_round_object()
                    review_assignment.access_code = uuid4()
                    review_assignment.save()

                    article.stage = submission_models.STAGE_UNDER_REVIEW
                    article.save()

                    kwargs = {'user_message_content': '',
                              'review_assignment': review_assignment,
                              'request': request,
                              'skip': False,
                              'acknowledgement': False}

                    event_logic.Events.raise_event(event_logic.Events.ON_REVIEWER_REQUESTED, **kwargs)

                    return redirect(reverse('review_notify_reviewer',
                                            kwargs={'article_id': article_id, 'review_id': review_assignment.id}))

    template = 'review/add_review_assignment.html'
    context = {
        'article': article,
        'form': form,
        'reviewers': reviewers,
        'new_reviewer_form': new_reviewer_form,
        'modal': modal,
        'user_list': user_list,
        'suggested_reviewers': suggested_reviewers,
    }

    return render(request, template, context)


@editor_is_not_author
@article_decision_not_made
@editor_user_required
def notify_reviewer(request, article_id, review_id):
    """
    Allows the editor to send a notification the the assigned peer reviewer
    :param request: HttpRequest object
    :param article_id: Articke PK
    :param review_id: ReviewAssignment PK
    :return: HttpResponse or HttpRedirect
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review = get_object_or_404(models.ReviewAssignment, pk=review_id)

    email_content = logic.get_reviewer_notification(request, article, request.user, review)

    if request.POST:
        email_content = request.POST.get('content_email')
        kwargs = {'user_message_content': email_content,
                  'review_assignment': review,
                  'request': request,
                  'skip': False,
                  'acknowledgement': True}

        if 'skip' in request.POST:
            kwargs['skip'] = True
            event_logic.Events.raise_event(event_logic.Events.ON_REVIEWER_REQUESTED_ACKNOWLEDGE, **kwargs)
            return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))

        event_logic.Events.raise_event(event_logic.Events.ON_REVIEWER_REQUESTED_ACKNOWLEDGE, **kwargs)

        review.date_requested = timezone.now()
        review.save()

        return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))

    template = 'review/notify_reviewer.html'
    context = {
        'article': article,
        'review': review,
        'email_content': email_content,
    }

    return render(request, template, context)


@editor_is_not_author
@editor_user_required
def view_review(request, article_id, review_id):
    """
    A view that allows the editor to view a review.
    :param request: Django's request object
    :param article_id: Article PK
    :param review_id: ReviewAssignment PK
    :return: a rendered django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review = get_object_or_404(models.ReviewAssignment, pk=review_id)

    if request.POST:
        if 'author_consumption' in request.POST:

            if review.for_author_consumption:
                review.for_author_consumption = False
            else:
                review.for_author_consumption = True
            review.save()

        if 'individual_author_consumption' in request.POST:
            checkboxes = request.POST.getlist('answer_viewable')

            for answer in review.review_form_answers():
                if str(answer.pk) in checkboxes:
                    answer.author_can_see = True
                else:
                    answer.author_can_see = False

                answer.save()

        if 'reset' in request.POST:
            answer_pk = request.POST.get('pk')
            answer = models.ReviewAssignmentAnswer.objects.get(pk=answer_pk)
            answer.edited_answer = None
            answer.save()

        if 'review_file_visible' in request.POST:
            logic.handle_review_file_switch(review, request.POST.get('review_file_visible'))
            messages.add_message(request, messages.SUCCESS, 'Review File visibility updated.')

        return redirect(reverse('review_view_review', kwargs={'article_id': article.pk, 'review_id': review.pk}))

    template = 'review/view_review.html'
    context = {
        'article': article,
        'review': review
    }

    return render(request, template, context)


@editor_is_not_author
@article_edit_user_required
def edit_review_answer(request, article_id, review_id, answer_id):
    """
    Allows an Editor to tweak an answer given for a peer review question.
    :param request: HttpRequest object
    :param article_id: Article PK
    :param review_id: ReviewAssignment PK
    :param answer_id: ReviewAssignmentAnswer PK
    :return: HttpResponse or HttpRedirect
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review = get_object_or_404(models.ReviewAssignment, pk=review_id)
    answer = get_object_or_404(models.ReviewAssignmentAnswer, pk=answer_id)

    form = forms.GeneratedForm(answer=answer)

    if request.POST:
        form = forms.GeneratedForm(request.POST, answer=answer)
        if form.is_valid():
            answer.edited_answer = form.cleaned_data[answer.element.name]
            answer.save()

            return redirect(reverse('review_view_review', kwargs={'article_id': article.pk, 'review_id': review.pk}))

    template = 'review/edit_review_answer.html'
    context = {
        'article': article,
        'review': review,
        'answer': answer,
        'form': form,
    }

    return render(request, template, context)


@editor_is_not_author
@article_decision_not_made
@editor_user_required
def edit_review(request, article_id, review_id):
    """
    A view that allows a user to edit a review.
    :param request: Django's request object
    :param article_id: Article PK
    :param review_id: ReviewAssignment PK
    :return: a rendered django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review = get_object_or_404(models.ReviewAssignment, pk=review_id)

    if review.date_complete:
        messages.add_message(request, messages.WARNING, 'You cannot edit a review that is already complete.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    form = forms.ReviewAssignmentForm(instance=review, journal=request.journal)

    if request.POST:
        form = forms.ReviewAssignmentForm(request.POST, instance=review, journal=request.journal)

        if form.is_valid():
            form.save()
            messages.add_message(request, messages.INFO, 'Review updates.')
            util_models.LogEntry.add_entry('Review Deleted', 'Review updated.', level='Info', actor=request.user,
                                           request=request, target=review)
            return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    template = 'review/edit_review.html'
    context = {
        'article': article,
        'review': review,
        'form': form,
    }

    return render(request, template, context)


@editor_is_not_author
@article_decision_not_made
@editor_user_required
def delete_review(request, article_id, review_id):
    """
    A view that allows a user to delete a review.
    :param request: Django's request object
    :param article_id: Article PK
    :param review_id: ReviewAssignment PK
    :return: a rendered django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review = get_object_or_404(models.ReviewAssignment, pk=review_id)

    if review.date_complete:
        messages.add_message(request, messages.WARNING, 'You cannot delete a review that is already complete.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    if request.POST and 'delete' in request.POST:
        user_message = request.POST.get('delete_rationale', 'No message supplied by user.')
        description = 'Review {0} for article {1} has been deleted by {2}. \n\n{3}'.format(
            review.pk,
            article.title,
            request.user.username,
            user_message,
        )
        util_models.LogEntry.add_entry('Review Deleted', description, level='Info', actor=request.user,
                                       request=request, target=article)

        review.delete()
        messages.add_message(request, messages.SUCCESS, 'Review deleted.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    template = 'review/delete_review.html'
    context = {
        'article': article,
        'review': review,
    }

    return render(request, template, context)


@editor_is_not_author
@article_decision_not_made
@editor_user_required
def withdraw_review(request, article_id, review_id):
    """
    A view that allows a user to withdraw a review.
    :param request: Django's request object
    :param article_id: Article PK
    :param review_id: ReviewAssignment PK
    :return:a rendered django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review = get_object_or_404(models.ReviewAssignment, pk=review_id)

    if review.date_complete:
        messages.add_message(request, messages.WARNING, 'You cannot withdraw a review that is already complete.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    email_content = logic.get_withdrawl_notification(request, review)

    if request.POST:
        email_content = request.POST.get('content_email')
        kwargs = {'user_message_content': email_content,
                  'review_assignment': review,
                  'request': request,
                  'skip': False}

        if 'skip' in request.POST:
            kwargs['skip'] = True

        event_logic.Events.raise_event(event_logic.Events.ON_REVIEW_WITHDRAWL, **kwargs)

        review.date_complete = timezone.now()
        review.decision = 'withdrawn'
        review.is_complete = True
        review.save()

        messages.add_message(request, messages.SUCCESS, 'Review withdrawn')
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    template = 'review/withdraw_review.html'
    context = {
        'article': article,
        'review': review,
        'email_content': email_content,
    }

    return render(request, template, context)


@editor_is_not_author
@article_decision_not_made
@editor_user_required
def reset_review(request, article_id, review_id):
    """
    Allows an editor to reset a review that has previously been declined or withdrawn.
    :param request: django Django's request object
    :param article_id: pk of an Article
    :param review_id: pk of a ReviewAssignment
    :return: a contextualised django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    review = get_object_or_404(models.ReviewAssignment, pk=review_id)

    if request.POST:
        review.is_complete = False
        review.date_complete = None
        review.date_declined = None
        review.decision = None
        review.suggested_reviewers = ""
        review.save()

        messages.add_message(request, messages.INFO, 'Review reset.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    template = 'review/reset.html'
    context = {
        'article': article,
        'review': review,
    }

    return render(request, template, context)


@section_editor_draft_decisions
@editor_is_not_author
@editor_user_required
def review_decision(request, article_id, decision):
    """
    Allows the editor to make a review decision, revisions are not a decision, only accept or delcine.
    :param request: the django request object
    :param article_id: Article PK
    :param decision
    :return: a contextualised django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    author_review_url = "{0}{1}".format(request.journal_base_url,
                                        reverse('review_author_view', kwargs={'article_id': article.id}))
    email_content = logic.get_decision_content(request, article, decision, author_review_url)

    if article.date_accepted or article.date_declined:
        messages.add_message(request, messages.WARNING, 'This article has already been accepted or declined.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    if request.POST:
        email_content = request.POST.get('decision_rationale')
        kwargs = {
            'article': article,
            'request': request,
            'decision': decision,
            'user_message_content': email_content,
            'skip': False,
        }

        if 'skip' in request.POST:
            kwargs['skip'] = True

        if decision == 'accept':
            article.accept_article(stage=submission_models.STAGE_EDITOR_COPYEDITING)
            article.snapshot_authors(article)
            event_logic.Events.raise_event(event_logic.Events.ON_ARTICLE_ACCEPTED, task_object=article, **kwargs)

            workflow_kwargs = {'handshake_url': 'review_unassigned_article',
                               'request': request,
                               'article': article,
                               'switch_stage': True}
            return event_logic.Events.raise_event(event_logic.Events.ON_WORKFLOW_ELEMENT_COMPLETE, task_object=article,
                                                  **workflow_kwargs)
        elif decision == 'decline':
            article.decline_article()
            event_logic.Events.raise_event(event_logic.Events.ON_ARTICLE_DECLINED, task_object=article, **kwargs)
            return redirect(reverse('core_dashboard'))

        messages.add_message(request, messages.INFO, 'Article {0} has been {1}ed'.format(article.title, decision))
        return redirect(reverse('article_copyediting', kwargs={'article_id': article.pk}))

    template = 'review/decision.html'
    context = {
        'article': article,
        'decision': decision,
        'email_content': email_content,
    }

    return render(request, template, context)


@editor_is_not_author
@article_edit_user_required
def rate_reviewer(request, article_id, review_id):
    """
    Allows an Editor to rate a Reviewer
    :param request: django's request object
    :param article_id: pk of a Article
    :param review_id: pk of a ReviewAssignment
    :return: a contextualised django template
    """

    review = get_object_or_404(models.ReviewAssignment, pk=review_id, article__pk=article_id)
    if not review.is_complete:
        messages.add_message(request, messages.INFO, 'You cannot rate a reviewer until their review is complete.'
                                                     'You should withdraw this review if you want to rate the reviewer'
                                                     'before they are finished.')
        return redirect(reverse('review_in_review', kwargs={'article_id': review.article.id}))

    if request.POST:
        rating_int = int(request.POST.get('rating_number'))

        if review.review_rating:
            rating = review.review_rating
            rating.rating = rating_int
            rating.save()
            messages.add_message(request, messages.INFO,
                                 '{0}\'s rating updated to {1}'.format(review.reviewer.full_name(), rating_int))
        else:
            messages.add_message(request, messages.INFO,
                                 '{0} assigned a rating of {1}'.format(review.reviewer.full_name(), rating_int))
            models.ReviewerRating.objects.create(assignment=review, rating=rating_int, rater=request.user)

        return redirect(reverse('review_in_review', kwargs={'article_id': review.article.id}))

    template = 'review/rate_reviewer.html'
    context = {
        'review': review,
    }

    return render(request, template, context)


@article_author_required
def author_view_reviews(request, article_id):
    """
    View that allows an author to view the reviews for an article.
    :param request: django request object
    :param article_id: Article pk
    :return: a contextualised django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    reviews = models.ReviewAssignment.objects.filter(article=article,
                                                     is_complete=True,
                                                     for_author_consumption=True).exclude(decision='withdrawn')

    template = 'review/author_view_reviews.html'
    context = {
        'article': article,
        'reviews': reviews,
    }

    return render(request, template, context)


@section_editor_draft_decisions
@editor_is_not_author
@article_edit_user_required
def request_revisions(request, article_id):
    """
    View allows an Editor to request revisions to an article.
    :param request: django request object
    :param article_id: Article PK
    :return: a contextualised django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    form = forms.RevisionRequest()

    if request.POST:
        form = forms.RevisionRequest(request.POST)

        if form.is_valid():
            revision_request = form.save(commit=False)
            revision_request.editor = request.user
            revision_request.article = article
            revision_request.save()

            article.stage = submission_models.STAGE_UNDER_REVISION
            article.save()

            return redirect(reverse('request_revisions_notification', kwargs={'article_id': article.pk,
                                                                              'revision_id': revision_request.pk}))

    template = 'review/revision/request_revisions.html'
    context = {
        'article': article,
        'form': form,
    }

    return render(request, template, context)


@section_editor_draft_decisions
@editor_is_not_author
@article_edit_user_required
def request_revisions_notification(request, article_id, revision_id):
    """
    View allows an Editor to notify an Author of a Revision request
    :param request: django request object
    :param article_id: PK of an Article
    :param revision_id: PK of a RevisionRequest
    :return: a contextualised django template
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)
    revision = get_object_or_404(models.RevisionRequest, pk=revision_id)
    email_content = logic.get_revision_request_content(request, article, revision)

    if request.POST:
        user_message_content = request.POST.get('email_content')

        kwargs = {
            'user_message_content': user_message_content,
            'revision': revision,
            'request': request,
            'skip': False,
        }

        if 'skip' in request.POST:
            kwargs['skip'] = True

        event_logic.Events.raise_event(event_logic.Events.ON_REVISIONS_REQUESTED_NOTIFY, **kwargs)

        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    template = 'review/revision/request_revisions_notification.html'
    context = {
        'article': article,
        'email_content': email_content,
        'revision': revision,
    }

    return render(request, template, context)


@editor_is_not_author
@article_edit_user_required
def edit_revision_request(request, article_id, revision_id):
    """
    View allows an Editor to edit an existing Revision
    :param request: HttpRequest object
    :param article_id: Artickle PK
    :param revision_id: Revision PK
    :return: HttpResponse
    """
    revision_request = get_object_or_404(models.RevisionRequest,
                                         article__pk=article_id,
                                         pk=revision_id)
    form = forms.EditRevisionDue(instance=revision_request)

    if revision_request.date_completed:
        messages.add_message(request, messages.WARNING, 'You cannot edit a revision request that is complete.')
        return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))

    if request.POST:

        if 'update_due' in request.POST:
            form = forms.EditRevisionDue(request.POST, instance=revision_request)

            if form.is_valid():
                form.save()
                messages.add_message(request, messages.INFO, 'Due date updated.')

        if 'delete_revision' in request.POST:
            rationale = request.POST.get('delete_rationale')
            util_models.LogEntry.add_entry('deletion', '{0} deleted a revision request with reason:\n\n{1}'.format(
                request.user.full_name(), rationale), level='Info', actor=request.user, target=revision_request.article
            )
            revision_request.delete()
            messages.add_message(request, messages.INFO, 'Revision request deleted.')

        if 'mark_as_complete' in request.POST:
            util_models.LogEntry.add_entry('update', '{0} marked revision {1} as complete'.format(
                request.user.full_name(), revision_request.id), level='Info', actor=request.user,
                target=revision_request.article
            )
            revision_request.date_completed = timezone.now()
            revision_request.save()
            messages.add_message(request, messages.INFO, 'Revision request marked as complete.')

        return redirect(reverse('review_in_review', kwargs={'article_id': article_id}))

    template = 'review/revision/edit_revision_request.html'
    context = {
        'revision_request': revision_request,
        'form': form,
    }

    return render(request, template, context)


@article_author_required
def do_revisions(request, article_id, revision_id):
    """
    Allows an Author to complete a revision request of an article.
    :param request: django request object
    :param article_id: PK of an Article
    :param revision_id: PK of a RevisionRequest
    :return:
    """
    revision_request = get_object_or_404(models.RevisionRequest,
                                         article__pk=article_id,
                                         pk=revision_id,
                                         date_completed__isnull=True)

    reviews = models.ReviewAssignment.objects.filter(article=revision_request.article,
                                                     is_complete=True,
                                                     for_author_consumption=True).exclude(decision='withdrawn')

    form = forms.DoRevisions(instance=revision_request)
    revision_files = logic.group_files(revision_request.article, reviews)

    if request.POST:

        if 'delete' in request.POST:
            file_id = request.POST.get('delete')
            file = get_object_or_404(core_models.File, pk=file_id)
            file.delete()
            logic.log_revision_event('File {0} ({1}) deleted.'.format(file.id, file.original_filename), request.user,
                                     revision_request)
            return redirect(reverse('do_revisions', kwargs={'article_id': article_id, 'revision_id': revision_id}))

        else:

            form = forms.DoRevisions(request.POST, instance=revision_request)
            if not revision_request.article.has_manuscript_file():
                form.add_error(None, 'Your article must have at least one manuscript file.')
            if form.is_valid():
                form.save()

                kwargs = {
                    'revision': revision_request,
                    'request': request,
                }

                event_logic.Events.raise_event(event_logic.Events.ON_REVISIONS_COMPLETE, **kwargs)

                revision_request.date_completed = timezone.now()
                revision_request.save()
                return redirect(reverse('review_requests'))

    if request.GET.get('file_id', None):
        file_id = request.GET.get('file_id')
        file = get_object_or_404(core_models.File, pk=file_id)
        if file in revision_files:
            logic.log_revision_event('Downloaded file {0} ({1}).'.format(file.label, file.original_filename),
                                     request.user, revision_request)
            return files.serve_file(request, file, revision_request.article)

    template = 'review/revision/do_revision.html'
    context = {
        'revision_request': revision_request,
        'form': form,
        'article': revision_request.article,
        'reviews': reviews,
    }

    return render(request, template, context)


@article_author_required
def replace_file(request, article_id, revision_id, file_id):
    revision_request = get_object_or_404(models.RevisionRequest, article__pk=article_id, pk=revision_id,
                                         date_completed__isnull=True)
    file = get_object_or_404(core_models.File, pk=file_id)

    if request.GET.get('download', None):
        logic.log_revision_event('Downloaded file {0} ({1})'.format(file.label, file.original_filename), request.user,
                                 revision_request)
        return files.serve_file(request, file, revision_request.article)

    if request.POST and request.FILES:

        if 'replacement' in request.POST:
            uploaded_file = request.FILES.get('replacement-file')
            label = request.POST.get('label')
            new_file = files.save_file_to_article(uploaded_file, revision_request.article, request.user,
                                                  replace=file, is_galley=False, label=label)

            files.replace_file(revision_request.article, file, new_file)
            logic.log_revision_event(
                'File {0} ({1}) replaced with {2} ({3})'.format(file.label, file.original_filename, new_file.label,
                                                                new_file.original_filename),
                request.user, revision_request)

        return redirect(reverse('do_revisions', kwargs={'article_id': article_id, 'revision_id': revision_id}))

    template = 'review/revision/replace_file.html'
    context = {
        'revision_request': revision_request,
        'article': revision_request.article,
        'file': file,
    }

    return render(request, template, context)


@article_author_required
def upload_new_file(request, article_id, revision_id):
    """
    View allows an author to upload  new file to their article.
    :param request: HttpRequest object
    :param article_id: Article PK
    :param revision_id: RevisionRequest PK
    :return: Httpresponse or HttpRedirect
    """
    revision_request = get_object_or_404(models.RevisionRequest, article__pk=article_id, pk=revision_id,
                                         date_completed__isnull=True)
    article = revision_request.article

    if request.POST:
        file_type = request.POST.get('file_type')
        uploaded_file = request.FILES.get('file')
        label = request.POST.get('label')
        new_file = files.save_file_to_article(uploaded_file, article, request.user, label=label)

        if file_type == 'manuscript':
            article.manuscript_files.add(new_file)

        if file_type == 'data':
            article.data_figure_files.add(new_file)

        logic.log_revision_event('New file {0} ({1}) uploaded'.format(new_file.label, new_file.original_filename),
                                 request.user, revision_request)

        return redirect(reverse('do_revisions', kwargs={'article_id': article_id, 'revision_id': revision_id}))

    template = 'review/revision/upload_file.html'
    context = {
        'revision_request': revision_request,
        'article': revision_request.article,
    }

    return render(request, template, context)


@editor_is_not_author
@editor_user_required
def view_revision(request, article_id, revision_id):
    """
    Allows an Editor to view a revisionrequest
    :param request: HttpRequest object
    :param article_id: Article PK
    :param revision_id: RevisionRequest PK
    :return: HttpResponse
    """
    revision_request = get_object_or_404(models.RevisionRequest.objects.select_related('article'),
                                         pk=revision_id,
                                         article__pk=article_id)

    template = 'review/revision/view_revision.html'
    context = {
        'revision_request': revision_request,
        'article': revision_request.article
    }

    return render(request, template, context)


@editor_user_required
def review_warning(request, article_id):
    """
    Checks if an editor user is the author of an article amd blocks their access temporarily.
    If overwritten, all Editors are notified.
    :param request: HttpRequest object
    :param article_id: Article PK
    :return: HttpResponse or HttpRedirect
    """
    article = get_object_or_404(submission_models.Article, pk=article_id)

    if request.POST and request.user.is_staff:
        override = models.EditorOverride.objects.create(article=article, editor=request.user)
        kwargs = {'request': request, 'override': override}
        event_logic.Events.raise_event(event_logic.Events.ON_REVIEW_SECURITY_OVERRIDE, task_object=article, **kwargs)
        return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))
    elif not request.user.is_staff:
        messages.add_message(request, messages.WARNING, 'You are not a staff member.')

    template = 'review/review_warning.html'
    context = {
        'article': article
    }

    return render(request, template, context)


@article_edit_user_required
@file_user_required
def editor_article_file(request, article_id, file_id):
    """ Serves an article file.

    :param request: the request associated with this call
    :param article_id: the id of an article
    :param file_id: the file ID to serve
    :return: a streaming response of the requested file or 404
    """
    article_object = submission_models.Article.objects.get(pk=article_id)
    file_object = get_object_or_404(core_models.File, pk=file_id)

    return files.serve_file(request, file_object, article_object)


@reviewer_user_for_assignment_required
def reviewer_article_file(request, assignment_id, file_id):
    """ Serves an article file.
    :param request: the request associated with this call
    :param assignment_id: the ReviewAssignment id.
    :param file_id: the file ID to serve
    :return: a streaming response of the requested file or 404
    """
    review_assignment = models.ReviewAssignment.objects.get(pk=assignment_id)
    article_object = review_assignment.article

    file_object = review_assignment.review_round.review_files.get(pk=file_id)

    if not file_object:
        raise Http404()

    return files.serve_file(request, file_object, article_object)


@reviewer_user_for_assignment_required
def review_download_all_files(request, assignment_id):
    review_assignment = models.ReviewAssignment.objects.get(pk=assignment_id)

    zip_file, file_name = files.zip_files(review_assignment.review_round.review_files.all())

    return files.serve_temp_file(zip_file, file_name)


@editor_is_not_author
@article_edit_user_required
def draft_decision(request, article_id):
    """
    Allows a section editor to draft a decision for an editor.
    :param request: request object
    :param article_id: an Article primary key
    :return: a django template with context
    """

    article = get_object_or_404(submission_models.Article, pk=article_id)
    drafts = models.DecisionDraft.objects.filter(article=article)
    email_message = logic.get_draft_email_message(request, article)

    form = forms.DraftDecisionForm(email_message=email_message)

    if request.POST:

        if 'delete' in request.POST:
            delete_id = request.POST.get('delete')
            draft = get_object_or_404(models.DecisionDraft, pk=delete_id, article=article)
            draft.delete()
            return redirect(reverse('review_draft_decision', kwargs={'article_id': article.pk}))

        else:
            form = forms.DraftDecisionForm(request.POST, email_message=email_message)

            if form.is_valid():
                new_draft = form.save(commit=False)

                new_draft.section_editor = request.user
                new_draft.article = article
                new_draft.save()
                messages.add_message(request, messages.SUCCESS, 'A draft has been saved, the editor has been notified.')

                kwargs = {'request': request, 'article': article, 'draft': new_draft}
                event_logic.Events.raise_event(event_logic.Events.ON_DRAFT_DECISION, **kwargs)

                return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))

    template = 'review/draft_decision.html'
    context = {
        'article': article,
        'drafts': drafts,
        'form': form,
    }

    return render(request, template, context)


@editor_is_not_author
@editor_user_required
def manage_draft(request, article_id, draft_id):
    article = get_object_or_404(submission_models.Article, pk=article_id)
    draft = get_object_or_404(models.DecisionDraft, pk=draft_id)

    if request.POST:
        draft.closed = True

    if 'decline_draft' in request.POST:
        draft.editor_decision = 'declined'
        draft.save()

    if 'accept_draft' in request.POST:
        # draft.editor_decision = 'accept'
        draft.save()

        # Action the decision
        logic.handle_decision_action(article, draft, request)

    return redirect(reverse('review_in_review', kwargs={'article_id': article.pk}))


@editor_is_not_author
@article_edit_user_required
def edit_draft_decision(request, article_id, draft_id):
    article = get_object_or_404(submission_models.Article, pk=article_id)
    draft = get_object_or_404(models.DecisionDraft, pk=draft_id)
    drafts = models.DecisionDraft.objects.filter(article=article)
    form = forms.DraftDecisionForm(instance=draft)

    if request.POST:
        form = forms.DraftDecisionForm(request.POST, instance=draft)

        if form.is_valid():
            form.save()
            messages.add_message(request, messages.SUCCESS, 'Draft has been updated')
            return redirect(reverse('review_edit_draft_decision',
                                    kwargs={'article_id': article.pk, 'draft_id': draft.pk}))

    template = 'review/draft_decision.html'
    context = {
        'article': article,
        'drafts': drafts,
        'draft': draft,
        'form': form,
    }

    return render(request, template, context)


@staff_member_required
def review_forms(request):
    """
    Displays a list of review formd and allows new ones to be created.
    :param request: HttpRequest object
    :return: HttpResponse or HttpRedirect
    """
    form_list = models.ReviewForm.objects.filter(journal=request.journal)

    form = forms.NewForm()

    if request.POST:
        form = forms.NewForm(request.POST)

        if form.is_valid():
            new_form = form.save(commit=False)
            new_form.journal = request.journal
            new_form.save()

            return redirect(reverse('review_review_forms'))

    template = 'review/manager/review_forms.html'
    context = {
        'form_list': form_list,
        'form': form,
    }

    return render(request, template, context)


@staff_member_required
def edit_review_form(request, form_id, element_id=None):
    """
    Allows the editing of an existing review form
    :param request: HttpRequest object
    :param form_id: ReviewForm PK
    :param element_id: Element PK, optional
    :return: HttpResponse or HttpRedirect
    """
    edit_form = get_object_or_404(models.ReviewForm, pk=form_id)

    form = forms.NewForm(instance=edit_form)
    element_form = forms.ElementForm()
    element, modal = None, None

    if element_id:
        element = get_object_or_404(models.ReviewFormElement, pk=element_id)
        modal = 'element'
        element_form = forms.ElementForm(instance=element)

    if request.POST:

        if 'delete' in request.POST:
            delete_id = request.POST.get('delete')
            element_to_delete = get_object_or_404(models.ReviewFormElement, pk=delete_id)
            element_to_delete.delete()
            return redirect(reverse('edit_review_form', kwargs={'form_id': edit_form.pk}))

        if 'element' in request.POST:
            if element_id:
                element_form = forms.ElementForm(request.POST, instance=element)
            else:
                element_form = forms.ElementForm(request.POST)

            if element_form.is_valid():
                element = element_form.save()
                edit_form.elements.add(element)
                messages.add_message(request, messages.SUCCESS, 'New element added.')
                return redirect(reverse('edit_review_form', kwargs={'form_id': edit_form.pk}))

        if 'review_form' in request.POST:
            form = forms.NewForm(request.POST, instance=edit_form)

            if form.is_valid():
                form.save()
                messages.add_message(request, messages.SUCCESS, 'Form updated')
                return redirect(reverse('edit_review_form', kwargs={'form_id': edit_form.pk}))

    template = 'review/manager/edit_review_form.html'
    context = {
        'form': form,
        'edit_form': edit_form,
        'element_form': element_form,
        'modal': modal,
    }

    return render(request, template, context)


@staff_member_required
def preview_form(request, form_id):
    """Displays a preview of a review form."""
    form = get_object_or_404(models.ReviewForm, pk=form_id)
    generated_form = forms.GeneratedForm(preview=form)

    template = 'review/manager/preview_form.html'
    context = {
        'form': form,
        'generated_form': generated_form,
    }

    return render(request, template, context)


@reviewer_user_for_assignment_required
def hypothesis_review(request, assignment_id):
    """
    Rendering of the review form for user to complete.
    :param request: the request object
    :param assignment_id: ReviewAssignment PK
    :return: a context for a Django template
    """

    access_code = logic.get_access_code(request)

    if access_code:
        assignment = models.ReviewAssignment.objects.get(
            Q(pk=assignment_id) &
            Q(is_complete=False) &
            Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
            Q(access_code=access_code)
        )
    else:
        assignment = models.ReviewAssignment.objects.get(
            Q(pk=assignment_id) &
            Q(is_complete=False) &
            Q(article__stage=submission_models.STAGE_UNDER_REVIEW) &
            Q(reviewer=request.user)
        )

    pdf = assignment.review_round.review_files.get(mime_type='application/pdf')
    hypothesis.create_hypothesis_account(assignment.reviewer)
    grant_token = hypothesis.generate_grant_token(assignment.reviewer)

    template = 'review/annotation_pdf_review.html'
    context = {
        'assignment': assignment,
        'pdf': pdf,
        'grant_token': grant_token,
        'authority': settings.HYPOTHESIS_CLIENT_AUTHORITY,
    }

    return render(request, template, context)
