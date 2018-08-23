__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"


from django.conf.urls import url

from preprint import views

urlpatterns = [

    url(r'^$',
        views.preprints_home,
        name='preprints_home'),

    url(r'^dashboard/$',
        views.preprints_dashboard,
        name='preprints_dashboard'),

    url(r'^dashboard/(?P<article_id>\d+)/$',
        views.preprints_author_article,
        name='preprints_author_article'),

    url(r'^about/$',
        views.preprints_about,
        name='preprints_about'),

    url(r'^search/$',
        views.preprints_search,
        name='preprints_search'),

    url(r'^search/(?P<search_term>.*)/$',
        views.preprints_search,
        name='preprints_search_with_term'),

    url(r'^view/(?P<article_id>\d+)/$',
        views.preprints_article,
        name='preprints_article'),

    url(r'^view/(?P<article_id>\d+)/pdf/$',
        views.preprints_pdf,
        name='preprints_pdf'),

    url(r'^list/$',
        views.preprints_list,
        name='preprints_list'),

    url(r'^list/(?P<subject_slug>[-\w]+)/$',
        views.preprints_list,
        name='preprints_list_subject'),

    url(r'^editors/$',
        views.preprints_editors,
        name='preprints_editors'),

    url(r'^submit/start/$',
        views.preprints_submit,
        name='preprints_submit'),

    url(r'^submit/(?P<article_id>\d+)/$',
        views.preprints_submit,
        name='preprints_submit_with_id'),

    url(r'^submit/(?P<article_id>\d+)/authors/$',
        views.preprints_authors,
        name='preprints_authors'),

    url(r'^submit/(?P<article_id>\d+)/files/$',
        views.preprints_files,
        name='preprints_files'),

    url(r'^submit/(?P<article_id>\d+)/review/$',
        views.preprints_review,
        name='preprints_review'),

    url(r'^manager/$',
        views.preprints_manager,
        name='preprints_manager'),

    url(r'^manager/(?P<article_id>\d+)/$',
        views.preprints_manager_article,
        name='preprints_manager_article'),

    url(r'^manager/(?P<article_id>\d+)/notification/$',
        views.preprints_notification,
        name='preprints_notification'),

    url(r'^manager/(?P<article_id>\d+)/comments/$',
        views.preprints_comments,
        name='preprints_comments'),

    url(r'^manager/settings/$',
        views.preprints_settings,
        name='preprints_settings'),

    url(r'^manager/subjects/$',
        views.preprints_subjects,
        name='preprints_subjects'),

    url(r'^manager/subjects/(?P<subject_id>\d+)/$',
        views.preprints_subjects,
        name='preprints_subjects_with_id'),

    url(r'^manager/rejected/$',
        views.preprints_rejected_submissions,
        name='preprints_rejected_submissions'),

    url(r'^manager/orphans/$',
        views.orphaned_preprints,
        name='preprints_orphaned_preprints'),

    url(r'^manager/versions/$',
        views.version_queue,
        name='version_queue'),

]
