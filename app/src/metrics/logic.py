__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"

import calendar
from datetime import timedelta
from user_agents import parse as parse_ua_string

from django.utils import timezone

from metrics import models
from utils import shared
from utils.function_cache import cache


@cache(300)
def get_press_totals(start_date, end_date, report_months, compat=False, do_yop=False):
    from submission import models as submission_models
    from journal import models as journal_models

    view_access_count = 0
    download_access_count = 0

    journals = []

    press_months = {}

    year_of_publication = {}

    for date in report_months:
        press_months['{0}-{1}'.format(date.strftime('%b'), date.year)] = ''

    for journal_object in journal_models.Journal.objects.all():

        journal = {}
        journal['journal'] = journal_object

        journal['total'] = 0
        journal['total_views'] = 0
        journal['total_downloads'] = 0

        journal['reporting_periods'] = []

        # year of publication is for COUNTER journal report 5
        # it needs to have "each YOP in the current decade and in the immediately previous decade as separate columns"
        # easiest way to do this is simply to count backwards 19 years as the theoretical maximum
        journal['year_of_publication'] = {}

        year = timezone.now().year

        for year_counter in range(year, year - 19, -1):
            year_of_publication[year] = ''

        for date in report_months:
            month = '{0}-{1}'.format(date.strftime('%b'), date.year)

            # setting these to zero for now for compat with pycounter
            # the spec says they should be set to "" so we may need to change that back
            # logic to handle this is included below
            if compat:
                journal[month] = 0
                journal['{0}-views'.format(month)] = 0
                journal['{0}-downloads'.format(month)] = 0
            else:
                journal[month] = ''
                journal['{0}-views'.format(month)] = ''
                journal['{0}-downloads'.format(month)] = ''

        articles = submission_models.Article.objects.filter(journal=journal_object)

        # if we're doing a year-of-publication for JR5 purposes, then run this section, which is pretty DB intensive
        if do_yop:
            for article in articles:
                views = get_article_views(article)
                downloads = get_article_downloads(article)

                if article.date_published.year in year_of_publication:
                    if year_of_publication[article.date_published.year] == '':
                        year_of_publication[article.date_published.year] = 0

                    if journal['year_of_publication'][article.date_published.year] == '':
                        journal['year_of_publication'][article.date_published.year] = 0

                year_of_publication[article.date_published.year] += views + downloads
                journal['year_of_publication'][article.date_published.year] += views + downloads

        for article in articles:
            views = models.ArticleAccess.objects.filter(type='view', article=article,
                                                        accessed__range=[start_date, end_date])

            downloads = models.ArticleAccess.objects.filter(type='download', article=article,
                                                            accessed__range=[start_date, end_date])

            view_count = views.count()
            download_count = downloads.count()

            # total views and downloads
            journal['total'] += view_count + download_count

            # total views
            journal['total_views'] += view_count

            # total downloads
            journal['total_downloads'] += download_count

            # add the totals to the press totals
            view_access_count += view_count
            download_access_count += download_count

            for view_object in views:
                # get the date for this access
                access_date = '{0}-{1}'.format(view_object.accessed.strftime('%b'), view_object.accessed.year)

                # we have to handle this like this since data for months already collected must be blank, not zero
                if journal[access_date] == '':
                    journal[access_date] = 0

                if press_months[access_date] == '':
                    press_months[access_date] = 0

                if journal['{0}-views'.format(access_date)] == '':
                    journal['{0}-views'.format(access_date)] = 0

                journal[access_date] += 1
                journal['{0}-views'.format(access_date)] += 1
                press_months[access_date] += 1

            for download_object in downloads:
                # get the date for this access
                access_date = '{0}-{1}'.format(download_object.accessed.strftime('%b'), download_object.accessed.year)

                # we have to handle this like this since data for months already collected must be blank, not zero
                if journal[access_date] == '':
                    journal[access_date] = 0

                if press_months[access_date] == '':
                    press_months[access_date] = 0

                if journal['{0}-downloads'.format(access_date)] == '':
                    journal['{0}-downloads'.format(access_date)] = 0

                journal[access_date] += 1
                journal['{0}-downloads'.format(access_date)] += 1
                press_months[access_date] += 1

        for date in report_months:
            # add to "reporting_periods":
            # a start date
            # an end date
            # a total number of views
            month = '{0}-{1}'.format(date.strftime('%b'), date.year)
            journal['reporting_periods'].append(('{0}-01'.format(date.strftime('%Y-%m')),
                                                 '{0}-{1}'.format(date.strftime('%Y-%m'),
                                                                  calendar.monthrange(date.year, date.month)[1]),
                                                 journal[month],
                                                 journal['{0}-views'.format(month)],
                                                 journal['{0}-downloads'.format(month)]))

        journals.append(journal)

    return view_access_count + download_access_count, view_access_count, download_access_count, press_months, journals


def get_article_views(article):
    historic_record, created = models.HistoricArticleAccess.objects.get_or_create(article=article)
    view_access_count = models.ArticleAccess.objects.filter(type='view', article=article).count()

    return historic_record.views + view_access_count


def get_article_downloads(article):
    historic_record, created = models.HistoricArticleAccess.objects.get_or_create(article=article)
    download_access_count = models.ArticleAccess.objects.filter(type='download', article=article).count()

    return historic_record.downloads + download_access_count


def get_altmetrics(article):
    alt_metrics = models.AltMetric.objects.filter(article=article)
    alm_dict = {}
    total = 0

    for metric in alt_metrics:
        if alm_dict.get(metric.source):
            alm_dict[metric.source] = alm_dict[metric.source] + 1
        else:
            alm_dict[metric.source] = 1
        total += 1

    alm_dict['total'] = total

    return alm_dict


class ArticleMetrics:
    views = 0
    downloads = 0
    alm = 0

    def __init__(self, article):
        self.views = get_article_views(article)
        self.downloads = get_article_downloads(article)
        self.alm = get_altmetrics(article)


def store_article_access(request, article, access_type, galley_type='view'):

    try:
        user_agent = parse_ua_string(request.META.get('HTTP_USER_AGENT', None))
    except TypeError:
        user_agent = None

    counter_tracking_id = request.session.get('counter_tracking')
    identifier = counter_tracking_id if counter_tracking_id else shared.get_ip_address(request)

    if user_agent and not user_agent.is_bot:

        # check if the current IP has accessed this article recently.
        time_to_check = timezone.now() - timedelta(seconds=30)
        check = models.ArticleAccess.objects.filter(identifier=identifier,
                                                    accessed__gte=time_to_check,
                                                    type=access_type,
                                                    galley_type=galley_type).count()

        if not check:

            new_access = models.ArticleAccess.objects.create(
                article=article,
                type=access_type,
                identifier=identifier,
                galley_type=galley_type
            )

            return new_access

        else:
            # get the most recent access attempt and reset its accessed to now.
            access = models.ArticleAccess.objects.filter(identifier=identifier,
                                                         accessed__gte=time_to_check,
                                                         type=access_type,
                                                         galley_type=galley_type).order_by('-accessed')[0]

            if access:
                access.accessed = timezone.now()
                access.save()

                return access

            else:
                return None

    else:

        return None


@cache(300)
def get_view_and_download_totals(articles):
    total_views = 0
    total_downs = 0

    for article in articles:
        total_views += article.metrics.views
        total_downs += article.metrics.downloads

    return total_views, total_downs