import re
import uuid
import os
import requests
from bs4 import BeautifulSoup
import html2text
import json
from dateutil import parser as dateparser

from django.utils import timezone

from utils.importers import shared
from submission import models
from journal import models as journal_models
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from utils import models as utils_models, setting_handler
from core import models as core_models, files as core_files
from review import models as review_models


# note: URL to pass for import is http://journal.org/jms/index.php/up/oai/


def get_thumbnails(url):
    """ Extract thumbnails from a Ubiquity Press site. This is run once per import to get the base thumbnail URL.

    :param url: the base URL of the journal
    :return: the thumbnail for this article
    """
    print("Extracting thumbnails.")

    url_to_use = url + '/articles/?f=1&f=3&f=2&f=4&f=5&order=date_published&app=100000'
    resp, mime = utils_models.ImportCacheEntry.fetch(url=url_to_use)

    soup = BeautifulSoup(resp)

    article = soup.find('div', attrs={'class': 'article-image'})
    article = BeautifulSoup(str(article))

    id_href = shared.get_soup(article.find('img'), 'src')

    if id_href.endswith('/'):
        id_href = id_href[:-1]
    id_href_split = id_href.split('/')
    id_href = id_href_split[:-1]
    id_href = '/'.join(id_href)[1:]

    return id_href


def import_article(journal, user, url, thumb_path=None):
    """ Import a Ubiquity Press article.

    :param journal: the journal to import to
    :param user: the user who will own the file
    :param url: the URL of the article to import
    :param thumb_path: the base path for thumbnails
    :return: None
    """

    # retrieve the remote page and establish if it has a DOI
    already_exists, doi, domain, soup_object = shared.fetch_page_and_check_if_exists(url)
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    if already_exists:
        # if here then this article has already been imported
        return

    # fetch basic metadata
    new_article = shared.get_and_set_metadata(journal, soup_object, user, False, True)

    # try to do a license lookup
    pattern = re.compile(r'creativecommons')
    license_tag = soup_object.find(href=pattern)
    license_object = models.Licence.objects.filter(url=license_tag['href'].replace('http:', 'https:'), journal=journal)

    if len(license_object) > 0 and license_object[0] is not None:
        license_object = license_object[0]
        print("Found a license for this article: {0}".format(license_object.short_name))
    else:
        license_object = models.Licence.objects.get(name='All rights reserved', journal=journal)
        print("Did not find a license for this article. Using: {0}".format(license_object.short_name))

    new_article.license = license_object

    # determine if the article is peer reviewed
    peer_reviewed = soup_object.find(name='a', text='Peer Reviewed') is not None
    print("Peer reviewed: {0}".format(peer_reviewed))

    new_article.peer_reviewed = peer_reviewed

    # get PDF and XML galleys
    pdf = shared.get_pdf_url(soup_object)

    # rip XML out if found
    pattern = re.compile('.*?XML.*')
    xml = soup_object.find('a', text=pattern)
    html = None

    if xml:
        print("Ripping XML")
        xml = xml.get('href', None).strip()
    else:
        # looks like there isn't any XML
        # instead we'll pull out any div with an id of "xml-article" and add as an HTML galley
        print("Ripping HTML")
        html = soup_object.find('div', attrs={'id': 'xml-article'})

        if html:
            html = str(html.contents[0])

    # attach the galleys to the new article
    galleys = {
        'PDF': pdf,
        'XML': xml,
        'HTML': html
    }

    shared.set_article_galleys_and_identifiers(doi, domain, galleys, new_article, url, user)

    # fetch thumbnails
    if thumb_path is not None:
        print("Attempting to assign thumbnail.")

        final_path_element = url.split('/')[-1]
        id_regex = re.compile(r'.*?(\d+)')
        matches = id_regex.match(final_path_element)
        article_id = matches.group(1)

        print("Determined remote article ID as: {0}".format(article_id))
        print("Thumbnail path: {thumb_path}, URL: {url}".format(thumb_path=thumb_path, url=url))

        try:
            filename, mime = shared.fetch_file(domain, thumb_path + "/" + article_id, "", 'graphic',
                                               new_article, user)
            shared.add_file(mime, 'graphic', 'Thumbnail', user, filename, new_article, thumbnail=True)
        except BaseException:
            print("Unable to import thumbnail. Recoverable error.")

    # lookup status
    stats = soup_object.findAll('div', {'class': 'stat-number'})

    # save the article to the database
    new_article.save()

    try:
        if stats:
            from metrics import models as metrics_models
            views = stats[0].contents[0]
            downloads = stats[1].contents[0]

            metrics_models.HistoricArticleAccess.objects.create(article=new_article,
                                                                views=views,
                                                                downloads=downloads)
    except BaseException:
        pass


def import_oai(journal, user, soup, domain):
    """ Initiate an OAI import on a Ubiquity Press journal.

        :param journal: the journal to import to
        :param user: the user who will own imported articles
        :param soup: the BeautifulSoup object of the OAI feed
        :param domain: the domain of the journal (for extracting thumbnails)
        :return: None
        """

    thumb_path = get_thumbnails(domain)

    identifiers = soup.findAll('dc:identifier')

    for identifier in identifiers:
        # rewrite the phrase /jms in Ubiquity Press OAI feeds to get version with
        # full and proper email metadata
        identifier.contents[0] = identifier.contents[0].replace('/jms', '')
        if identifier.contents[0].startswith('http'):
            print('Parsing {0}'.format(identifier.contents[0]))

            import_article(journal, user, identifier.contents[0], thumb_path)

    import_issue_images(journal, user, domain[:-1])
    import_journal_metadata(journal, user, domain[:-1])


def import_journal_metadata(journal, user, url):
    base_url = url

    issn = re.compile(r'E-ISSN: (\d{4}-\d{4})')
    publisher = re.compile(r'Published by (.*)')

    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    print("Extracting journal-level metadata...")

    resp, mime = utils_models.ImportCacheEntry.fetch(url=base_url)

    soup = BeautifulSoup(resp, 'lxml')

    issn_result = soup.find(text=issn)
    issn_match = issn.match(str(issn_result).strip())

    print('ISSN set to: {0}'.format(issn_match.group(1)))
    journal.issn = issn_match.group(1)

    try:
        publisher_result = soup.find(text=publisher)
        publisher_match = str(publisher_result.next_sibling.getText()).strip()
        print('Publisher set to: {0}'.format(publisher_match))
        journal.publisher = publisher_match
        journal.save()
    except BaseException:
        print("Error setting publisher.")


def parse_backend_list(url, auth_file, auth_url, regex):
    html_body, mime = utils_models.ImportCacheEntry.fetch(url, up_base_url=auth_url, up_auth_file=auth_file)

    matches = re.findall(regex, html_body.decode())

    # look for next_page
    soup_object = BeautifulSoup(html_body, 'lxml')
    soup = soup_object.find(text='>')

    if soup:
        href = soup.parent.attrs['href']
        matches += parse_backend_list(href, auth_file, auth_url, regex)

    return matches


def get_article_list(url, list_type, auth_file):
    auth_url = url

    regex = '\/jms\/editor\/submissionReview\/(\d+)'

    if list_type == 'in_review':
        url += '/jms/editor/submissions/submissionsInReview'
        regex = '\/jms\/editor\/submissionReview\/(\d+)'
    elif list_type == 'unassigned':
        url += '/jms/editor/submissions/submissionsUnassigned'
        regex = '\/jms\/editor\/submission\/(\d+)'
    elif list_type == 'in_editing':
        url += '/jms/editor/submissions/submissionsInEditing'
        regex = '\/jms\/editor\/submissionEditing\/(\d+)'
    elif list_type == 'archive':
        url += '/jms/editor/submissions/submissionsArchives'
        regex = '\/jms\/editor\/submissionEditing\/(\d+)'
    else:
        return None

    matches = parse_backend_list(url, auth_file, auth_url, regex)

    return matches


def parse_backend_user_list(url, auth_file, auth_url, regex):
    html_body, mime = utils_models.ImportCacheEntry.fetch(url, up_base_url=auth_url, up_auth_file=auth_file)

    matches = re.findall(regex, html_body.decode())

    # look for next_page
    soup_object = BeautifulSoup(html_body, 'lxml')
    soup = soup_object.find(text='>')

    if soup:
        href = soup.parent.attrs['href']
        matches += parse_backend_user_list(href, auth_file, auth_url, regex)

    return matches


def get_user_list(url, auth_file):
    auth_url = url

    url += '/manager/people/all'
    regex = '\/manager\/userProfile\/(\d+)'

    matches = parse_backend_user_list(url, auth_file, auth_url, regex)

    return matches


def map_review_recommendation(recommentdation):
    recommendations = {
        '2': 'minor_revisions',
        '3': 'major_revisions',
        '5': 'reject',
        '1': 'accept'
    }

    return recommendations.get(recommentdation, None)


def import_issue_images(journal, user, url):
    base_url = url

    if not url.endswith('/issue/archive/'):
        url += '/issue/archive/'

    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    resp, mime = utils_models.ImportCacheEntry.fetch(url=url)

    soup = BeautifulSoup(resp, 'lxml')

    import core.settings
    import os
    from django.core.files import File

    for issue in journal.issues():
        pattern = re.compile(r'\/\d+\/volume\/{0}\/issue\/{1}'.format(issue.volume, issue.issue))

        img_url_suffix = soup.find(src=pattern)

        if img_url_suffix:
            img_url = base_url + img_url_suffix.get('src')
            print("Fetching {0}".format(img_url))

            resp, mime = utils_models.ImportCacheEntry.fetch(url=img_url)

            path = os.path.join(core.settings.BASE_DIR, 'files', 'journals', str(journal.id))

            os.makedirs(path, exist_ok=True)

            path = os.path.join(path, 'volume{0}_issue_{0}.graphic'.format(issue.volume, issue.issue))

            with open(path, 'wb') as f:
                f.write(resp)

            with open(path, 'rb') as f:
                issue.cover_image.save(path, File(f))

            sequence_pattern = re.compile(r'.*?(\d+)\/volume\/{0}\/issue\/{1}.*'.format(issue.volume, issue.issue))

            issue.order = int(sequence_pattern.match(img_url).group(1))

            print("Setting Volume {0}, Issue {1} sequence to: {2}".format(issue.volume, issue.issue, issue.order))

            print("Extracting section orders within the issue...")

            new_url = '/{0}/volume/{1}/issue/{2}/'.format(issue.order, issue.volume, issue.issue)
            resp, mime = utils_models.ImportCacheEntry.fetch(url=base_url + new_url)

            soup_issue = BeautifulSoup(resp, 'lxml')

            sections_to_order = soup_issue.find_all(name='h2', attrs={'class': 'main-color-text'})

            section_order = 0

            # delete existing order models for sections for this issue
            journal_models.SectionOrdering.objects.filter(issue=issue).delete()

            for section in sections_to_order:
                print('[{0}] {1}'.format(section_order, section.getText()))
                order_section, c = models.Section.objects.language('en').get_or_create(
                    name=section.getText().strip(),
                    journal=journal)
                journal_models.SectionOrdering.objects.create(issue=issue,
                                                              section=order_section,
                                                              order=section_order).save()
                section_order += 1

            print("Extracting article orders within the issue...")

            # delete existing order models for issue
            journal_models.ArticleOrdering.objects.filter(issue=issue).delete()

            pattern = re.compile(r'\/articles\/(.+?)/(.+?)/')
            articles = soup_issue.find_all(href=pattern)

            article_order = 0

            processed = []

            for article_link in articles:
                # parse the URL into a DOI and prefix
                match = pattern.match(article_link['href'])
                prefix = match.group(1)
                doi = match.group(2)

                # get a proper article object
                article = models.Article.get_article(journal, 'doi', '{0}/{1}'.format(prefix, doi))

                if article and article not in processed:
                    journal_models.ArticleOrdering.objects.create(issue=issue,
                                                                  article=article,
                                                                  section=article.section,
                                                                  order=article_order)

                    article_order += 1

                processed.append(article)

            issue.save()


def import_jms_user(url, journal, auth_file, base_url, user_id):
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    # Fetch the user profile page and parse its metdata
    resp, mime = utils_models.ImportCacheEntry.fetch(url=url, up_auth_file=auth_file, up_base_url=base_url)
    soup_user_profile = BeautifulSoup(resp, 'lxml')
    profile_dict = shared.get_user_profile(soup_user_profile)[0]

    # add an account for this new user
    account = core_models.Account.objects.filter(email=profile_dict['email'])

    if account is not None and len(account) > 0:
        account = account[0]
        print("Found account for {0}".format(profile_dict['email']))
    else:
        print("Didn't find account for {0}. Creating.".format(profile_dict['email']))

        if profile_dict['Country'] == '—':
            profile_dict['Country'] = None
        else:
            try:
                profile_dict['Country'] = core_models.Country.objects.get(name=profile_dict['Country'])
            except BaseException:
                print("Country not found")
                profile_dict['Country'] = None

        if not profile_dict.get('Salutation') in dict(core_models.SALUTATION_CHOICES):
            profile_dict['Salutation'] = ''

        if profile_dict.get('Middle Name', None) == '-':
            profile_dict['Middle Name'] = ''

        account = core_models.Account.objects.create(email=profile_dict['email'],
                                                     username=profile_dict['Username'],
                                                     institution=profile_dict['Affiliation'],
                                                     first_name=profile_dict['First Name'],
                                                     last_name=profile_dict['Last Name'],
                                                     middle_name=profile_dict.get('Middle Name', None),
                                                     country=profile_dict.get('Country', None),
                                                     biography=profile_dict.get('Bio Statement', None),
                                                     salutation=profile_dict.get('Salutation', None),
                                                     is_active=True)
        account.save()

        if account:
            account.add_account_role(journal=journal, role_slug='author')
            account.add_account_role(journal=journal, role_slug='reviewer')


def process_resp(resp):
    resp = resp.decode("utf-8")

    known_strings = {
        '\\u00a0': " ",
        '\\u00e0': "à",
        '\\u0085': "...",
        '\\u0091': "'",
        '\\u0092': "'",
        '\\u0093': '\\"',
        '\\u0094': '\\"',
        '\\u0096': "-",
        '\\u0097': "-",
        '\\u00F6': 'ö',
        '\\u009a': 'š',
        '\\u00FC': 'ü',
    }

    for string, replacement in known_strings.items():
        resp = resp.replace(string, replacement)
    return resp


def ojs_plugin_import_review_articles(url, journal, auth_file, base_url):
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    resp, mime = utils_models.ImportCacheEntry.fetch(url=url, up_auth_file=auth_file, up_base_url=base_url)

    resp = process_resp(resp)

    _dict = json.loads(resp)

    for article_dict in _dict:
        create_article_with_review_content(article_dict, journal, auth_file, base_url)
        print('Importing {article}.'.format(article=article_dict.get('title')))


def ojs_plugin_import_editing_articles(url, journal, auth_file, base_url):
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
    resp, mime = utils_models.ImportCacheEntry.fetch(url=url, up_auth_file=auth_file, up_base_url=base_url)

    resp = process_resp(resp)

    _dict = json.loads(resp)

    for article_dict in _dict:
        print('#{id} {article}.'.format(id=article_dict.get('ojs_id'), article=article_dict.get('title')))
        article = create_article_with_review_content(article_dict, journal, auth_file, base_url)
        complete_article_with_production_content(article, article_dict, journal, auth_file, base_url)


def create_article_with_review_content(article_dict, journal, auth_file, base_url):
    date_started = timezone.make_aware(dateparser.parse(article_dict.get('date_submitted')))

    # Create a base article
    article = models.Article(
        journal=journal,
        title=article_dict.get('title'),
        abstract=article_dict.get('abstract'),
        language=article_dict.get('language'),
        stage=models.STAGE_UNDER_REVIEW,
        is_import=True,
        date_submitted=date_started,
    )

    article.save()

    # Check for editors and assign them as section editors.
    editors = article_dict.get('editors', [])

    for editor in editors:
        try:
            account = core_models.Account.objects.get(email=editor)
            account.add_account_role('section-editor', journal)
            review_models.EditorAssignment.objects.create(article=article, editor=account, editor_type='section-editor')
            print('Editor added to article')
        except BaseException:
            print('Editor account was not found.')

    # Add a new review round
    round = review_models.ReviewRound.objects.create(article=article, round_number=1)

    # Add keywords
    keywords = article_dict.get('keywords')
    if keywords:
        for keyword in keywords.split(';'):
            word, created = models.Keyword.objects.get_or_create(word=keyword)
            article.keywords.add(word)

    # Add authors
    for author in article_dict.get('authors'):
        try:
            author_record = core_models.Account.objects.get(email=author.get('email'))
        except core_models.Account.DoesNotExist:
            author_record = core_models.Account.objects.create(
                email=author.get('email'),
                first_name=author.get('first_name'),
                last_name=author.get('last_name'),
                institution=author.get('affiliation'),
                biography=author.get('bio'),
            )

        # If we have a country, fetch its record
        if author.get('country'):
            try:
                country = core_models.Country.objects.get(code=author.get('country'))
                author_record.country = country
                author_record.save()
            except core_models.Country.DoesNotExist:
                pass
        # Add authors to m2m and create an order record
        article.authors.add(author_record)
        models.ArticleAuthorOrder.objects.create(article=article,
                                                 author=author_record,
                                                 order=article.next_author_sort())

        # Set the primary author
        article.owner = core_models.Account.objects.get(email=article_dict.get('correspondence_author'))
        article.correspondence_author = article.owner

        # Get or create the article's section
        try:
            section = models.Section.objects.language().fallbacks('en').get(journal=journal,
                                                                            name=article_dict.get('section'))
        except models.Section.DoesNotExist:
            section = None

        article.section = section

        article.save()

    # Attempt to get the default review form
    form = setting_handler.get_setting('general',
                                       'default_review_form',
                                       journal,
                                       create=True).processed_value

    if not form:
        try:
            form = review_models.ReviewForm.objects.filter(journal=journal)[0]
        except BaseException:
            form = None
            print('You must have at least one review form for the journal before importing.')
            exit()

    for review in article_dict.get('reviews'):
        try:
            reviewer = core_models.Account.objects.get(email=review.get('email'))
        except core_models.Account.DoesNotExist:
            reviewer = core_models.Account.objects.create(
                email=review.get('email'),
                first_name=review.get('first_name'),
                last_name=review.get('last_name'),
            )

        # Parse the dates
        date_requested = timezone.make_aware(dateparser.parse(review.get('date_requested')))
        date_due = timezone.make_aware(dateparser.parse(review.get('date_due')))
        date_complete = timezone.make_aware(dateparser.parse(review.get('date_complete'))) if review.get(
            'date_complete') else None
        date_confirmed = timezone.make_aware(dateparser.parse(review.get('date_confirmed'))) if review.get(
            'date_confirmed') else None

        # If the review was declined, setup a date declined date stamp
        review.get('declined')
        if review.get('declined') == '1':
            date_declined = date_confirmed
            date_accepted = None
            date_complete = date_confirmed
        else:
            date_accepted = date_confirmed
            date_declined = None

        new_review = review_models.ReviewAssignment.objects.create(
            article=article,
            reviewer=reviewer,
            review_round=round,
            review_type='traditional',
            visibility='double-blind',
            date_due=date_due,
            date_requested=date_requested,
            date_complete=date_complete,
            date_accepted=date_accepted,
            access_code=uuid.uuid4(),
            form=form
        )

        if review.get('declined') or review.get('recommendation'):
            new_review.is_complete = True

        if review.get('recommendation'):
            new_review.decision = map_review_recommendation(review.get('recommendation'))

        if review.get('review_file_url'):
            filename, mime = shared.fetch_file(base_url, review.get('review_file_url'), None, None, article, None,
                                               handle_images=False, auth_file=auth_file)
            extension = os.path.splitext(filename)[1]

            review_file = shared.add_file(mime, extension, 'Reviewer file', reviewer, filename, article,
                                          galley=False)
            new_review.review_file = review_file

        if review.get('comments'):
            filepath = core_files.create_temp_file(review.get('comments'), 'comment.txt')
            file = open(filepath, 'r')
            comment_file = core_files.save_file_to_article(file,
                                                           article,
                                                           article.owner,
                                                           label='Review Comments',
                                                           save=False)
            import shutil
            directory = os.path.dirname(comment_file.self_article_path())
            if not os.path.exists(directory):
                os.makedirs(directory)

            shutil.copy(filepath, comment_file.self_article_path())
            new_review.review_file = comment_file

        new_review.save()

    # Get MS File
    ms_file = get_ojs_file(base_url, article_dict.get('manuscript_file_url'), article, auth_file, 'MS File')
    article.manuscript_files.add(ms_file)

    # Get RV File
    rv_file = get_ojs_file(base_url, article_dict.get('review_file_url'), article, auth_file, 'RV File')
    round.review_files.add(rv_file)

    # Get Supp Files
    if article_dict.get('supp_files'):
        for file in article_dict.get('supp_files'):
            file = get_ojs_file(base_url, file.get('url'), article, auth_file, file.get('title'))
            article.data_figure_files.add(file)

    article.save()
    round.save()

    return article


def get_ojs_file(base_url, url, article, auth_file, label):
    filename, mime = shared.fetch_file(base_url, url, None, None, article, None, handle_images=False,
                                       auth_file=auth_file)
    extension = os.path.splitext(filename)[1]
    file = shared.add_file(mime, extension, label, article.owner, filename, article, galley=False)

    return file


def determine_production_stage(article_dict):
    stage = models.STAGE_AUTHOR_COPYEDITING

    publication = True if article_dict.get('publication') and article_dict['publication'].get('date_published') else False
    typesetting = True if article_dict.get('layout') and article_dict['layout'].get('galleys') else False
    proofing = True if typesetting and article_dict.get('proofing') else False

    print(typesetting, proofing, publication)

    if publication:
        stage = models.STAGE_READY_FOR_PUBLICATION
    elif typesetting and not proofing:
        stage = models.STAGE_TYPESETTING
    elif proofing:
        stage = models.STAGE_PROOFING

    return stage


def attempt_to_make_timezone_aware(datetime):
    if datetime:
        dt = dateparser.parse(datetime)
        return timezone.make_aware(dt)
    else:
        return None


def import_copyeditors(article, article_dict, auth_file, base_url):
    copyediting = article_dict.get('copyediting', None)

    if copyediting:

        initial = copyediting.get('initial')
        author = copyediting.get('author')
        final = copyediting.get('final')

        from copyediting import models

        if initial:
            initial_copyeditor = core_models.Account.objects.get(email=initial.get('email'))
            initial_decision = True if (initial.get('underway') or initial.get('complete')) else False

            print('Adding copyeditor: {copyeditor}'.format(copyeditor=initial_copyeditor.full_name()))

            assigned = attempt_to_make_timezone_aware(initial.get('notified'))
            underway = attempt_to_make_timezone_aware(initial.get('underway'))
            complete = attempt_to_make_timezone_aware(initial.get('complete'))

            copyedit_assignment = models.CopyeditAssignment.objects.create(
                article=article,
                copyeditor=initial_copyeditor,
                assigned=assigned,
                notified=True,
                decision=initial_decision,
                date_decided=underway if underway else complete,
                copyeditor_completed=complete,
                copyedit_accepted=complete
            )

            if initial.get('file'):
                file = get_ojs_file(base_url, initial.get('file'), article, auth_file, 'Copyedited File')
                copyedit_assignment.copyeditor_files.add(file)

            if initial and author.get('notified'):
                print('Adding author review.')
                assigned = attempt_to_make_timezone_aware(author.get('notified'))
                complete = attempt_to_make_timezone_aware(author.get('complete'))

                author_review = models.AuthorReview.objects.create(
                    author=article.owner,
                    assignment=copyedit_assignment,
                    assigned=assigned,
                    notified=True,
                    decision='accept',
                    date_decided=complete,
                )

                if author.get('file'):
                    file = get_ojs_file(base_url, author.get('file'), article, auth_file, 'Author Review File')
                    author_review.files_updated.add(file)

            if final and initial_copyeditor and final.get('notified'):
                print('Adding final copyedit assignment.')

                assigned = attempt_to_make_timezone_aware(initial.get('notified'))
                underway = attempt_to_make_timezone_aware(initial.get('underway'))
                complete = attempt_to_make_timezone_aware(initial.get('complete'))

                final_decision = True if underway or complete else False

                final_assignment = models.CopyeditAssignment.objects.create(
                    article=article,
                    copyeditor=initial_copyeditor,
                    assigned=assigned,
                    notified=True,
                    decision=final_decision,
                    date_decided=underway if underway else complete,
                    copyeditor_completed=complete,
                    copyedit_accepted=complete,
                )

                if final.get('file'):
                    file = get_ojs_file(base_url, final.get('file'), article, auth_file, 'Final File')
                    final_assignment.copyeditor_files.add(file)


def import_typesetters(article, article_dict, auth_file, base_url):
    layout = article_dict.get('layout')
    task = None

    if layout.get('email'):
        typesetter = core_models.Account.objects.get(email=layout.get('email'))

        print('Adding typesetter {name}'.format(name=typesetter.full_name()))

        from production import models as production_models

        assignment = production_models.ProductionAssignment.objects.create(
            article=article,
            assigned=timezone.now(),
            notified=True
        )

        assigned = attempt_to_make_timezone_aware(layout.get('notified'))
        accepted = attempt_to_make_timezone_aware(layout.get('underway'))
        complete = attempt_to_make_timezone_aware(layout.get('complete'))

        task = production_models.TypesetTask.objects.create(
            assignment=assignment,
            typesetter=typesetter,
            assigned=assigned,
            accepted=accepted,
            completed=complete,
        )

    galleys = import_galleys(article, layout, auth_file, base_url)

    if task and galleys:
        for galley in galleys:
            task.galleys_loaded.add(galley.file)


def import_proofing(article, article_dict, auth_file, base_url):
    pass


def import_galleys(article, layout_dict, auth_file, base_url):
    galleys = list()

    if layout_dict.get('galleys'):

        for galley in layout_dict.get('galleys'):
            print('Adding Galley with label {label}'.format(label=galley.get('label')))
            file = get_ojs_file(base_url, galley.get('file'), article, auth_file, galley.get('label'))

            new_galley = core_models.Galley.objects.create(
                article=article,
                file=file,
                label=galley.get('label'),
            )

            galleys.append(new_galley)

    return galleys


def process_for_copyediting(article, article_dict, auth_file, base_url):
    import_copyeditors(article, article_dict, auth_file, base_url)


def process_for_typesetting(article, article_dict, auth_file, base_url):
    import_copyeditors(article, article_dict, auth_file, base_url)
    import_typesetters(article, article_dict, auth_file, base_url)


def process_for_proofing(article, article_dict, auth_file, base_url):
    import_copyeditors(article, article_dict, auth_file, base_url)
    import_typesetters(article, article_dict, auth_file, base_url)
    import_galleys(article, article_dict, auth_file, base_url)
    import_proofing(article, article_dict, auth_file, base_url)


def process_for_publication(article, article_dict, auth_file, base_url):
    process_for_proofing(article, article_dict, auth_file, base_url)
    # mark proofing complete


def complete_article_with_production_content(article, article_dict, journal, auth_file, base_url):
    """
    Completes the import of journal article that are in editing
    """
    article.stage = determine_production_stage(article_dict)
    article.save()

    print('Stage: {stage}'.format(stage=article.stage))

    if article.stage == models.STAGE_READY_FOR_PUBLICATION:
        process_for_publication(article, article_dict, auth_file, base_url)
    elif article.stage == models.STAGE_TYPESETTING:
        process_for_typesetting(article, article_dict, auth_file, base_url)
    else:
        process_for_copyediting(article, article_dict, auth_file, base_url)
