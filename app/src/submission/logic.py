__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"


from bs4 import BeautifulSoup

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.contrib import messages

from core import files
from core import models as core_models
from utils import setting_handler
from submission import models


def add_self_as_author(user, article):
    new_author = user
    article.authors.add(new_author)
    models.ArticleAuthorOrder.objects.create(article=article, author=new_author)

    return new_author


def check_author_exists(email):
    try:
        author = core_models.Account.objects.get(email=email)
        return author
    except core_models.Account.DoesNotExist:
        return False


def get_author(request, article):
    author_id = request.GET.get('author')
    frozen_authors = article.frozen_authors()
    try:
        author = frozen_authors.get(pk=author_id)
        return [author, 'author']
    except core_models.Account.DoesNotExist:
        return [None, None]


def get_agreement_text(journal):
    pub_fees = setting_handler.get_setting('general', 'publication_fees', journal).value
    sub_check = setting_handler.get_setting('general', 'submission_checklist', journal).value
    copy_notice = setting_handler.get_setting('general', 'copyright_notice', journal).value

    return "{0}\n\n{1}\n\n{2}".format(pub_fees, sub_check, copy_notice)


def check_file(uploaded_file, request, form):

    if not uploaded_file:
        form.add_error(None, 'You must select a file.')
        return False

    submission_formats = setting_handler.get_setting('general', 'limit_manuscript_types', request.journal).value

    if submission_formats:
        mime = files.guess_mime(str(uploaded_file.name))

        if mime in files.EDITABLE_FORMAT:
            return True
        else:
            form.add_error(None, 'You must upload a file that is either a Doc, Docx, RTF or ODT.')
            return False
    else:
        return True


def get_text(soup, to_find):
    try:
        return soup.find(to_find).text
    except AttributeError:
        return ''


def parse_authors(soup):
    authors = soup.find_all('contrib')
    author_list = []
    for author in authors:
        first_name = get_text(author, 'given-names')
        last_name = get_text(author, 'surname')
        email = get_text(author, 'email')

        try:
            aff_id = author.find('xref').get('rid', None)
            aff = author.find('aff', attrs={'id': aff_id}).text
        except AttributeError:
            aff = get_text(author, 'aff')

        author_list.append({'first_name': first_name, 'last_name': last_name, 'email': email, 'institution': aff})

    return author_list


def import_from_jats_xml(path, journal):
    with open(path) as file:
        soup = BeautifulSoup(file, 'lxml-xml')
        title = get_text(soup, 'article-title')
        abstract = get_text(soup, 'abstract')
        authors = parse_authors(soup)
        section = get_text(soup, 'subj-group')

        section_obj, created = models.Section.objects.language('en').get_or_create(name=section, journal=journal)

        article = models.Article.objects.create(
            title=title,
            abstract=abstract,
            section=section_obj,
            journal=journal,
        )

        for author in authors:
            if not author.get('email') or author.get('email') == '':
                author['email'] = '{first}.{last}@journal.com'.format(first=author.get('first_name'),
                                                                      last=author.get('last_name'))
            try:
                author = core_models.Account.objects.get(Q(email=author['email']) | Q(username=author['email']))
            except core_models.Account.DoesNotExist:
                author = core_models.Account.objects.create(
                    email=author['email'],
                    username=author['email'],
                    first_name=author['first_name'],
                    last_name=author['last_name'],
                    institution=author['institution']
                )
            article.authors.add(author)

        return article


def get_current_field(request, field_id):
    """
    Fetches a field based on whether there is a journal or press in the request object
    :param request: HttpRequest
    :param field_id: Field PK
    :return: Field Object
    """
    if field_id:
        if request.journal:
            field = get_object_or_404(models.Field, pk=field_id, journal=request.journal)
        else:
            field = get_object_or_404(models.Field, pk=field_id, press=request.press)
    else:
        field = None

    return field


def get_submission_fields(request):
    """
    Gets a queryset of fields base on whether there is a journal or press object present in request
    :param request: HttpRequest
    :return: A queryset of Field objects
    """

    if request.journal:
        fields = models.Field.objects.filter(journal=request.journal)
    else:
        fields = models.Field.objects.filter(press=request.press)

    return fields


def save_field(request, form):
    """
    Saves a form field and sets the press or journal parameter.
    :param request:
    :param form:
    :return:
    """

    new_field = form.save(commit=False)

    if request.journal:
        new_field.journal = request.journal
    else:
        new_field.press = request.press

    new_field.save()
    messages.add_message(request, messages.SUCCESS, 'Field saved.')
    return new_field


def delete_field(request):
    """
    Deletes a field object
    :param request: HttpRequest
    :return: None, adds a Message obejct to request
    """

    delete_id = request.POST.get('delete')
    field_to_delete = get_current_field(request, delete_id)
    field_to_delete.delete()
    messages.add_message(request, messages.SUCCESS, 'Field deleted. Existing answers will remain intact.')


def order_fields(request, fields):
    """
    Orders fields from a jquery sortable post.
    :param request: HttpRequest
    :param fields: Queryset of fields for this object
    :return: None
    """

    ids = [int(_id) for _id in request.POST.getlist('order[]')]

    for field in fields:
        order = ids.index(field.pk)
        field.order = order
        field.save()
