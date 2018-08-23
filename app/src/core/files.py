__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"

import mimetypes as mime
import os
from uuid import uuid4
from wsgiref.util import FileWrapper
from bs4 import BeautifulSoup
from lxml import etree
import re
import shutil
import magic
import tempfile
import hashlib

from django.conf import settings
from django.contrib import messages
from django.http import StreamingHttpResponse, HttpResponseRedirect, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from django.http import Http404
from django.views.decorators.cache import cache_control

from utils import models as util_models


EDITABLE_FORMAT = (
    'application/rtf',
    'application/x-rtf',
    'text/richtext',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.oasis.opendocument.text',
)

IMAGE_MIMETYPES = (
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/tiff',
)


def mkdirs(path):
    if not os.path.exists(path):
        os.makedirs(path)


def create_temp_file(content, filename):
    filename = '{uuid}-{filename}'.format(uuid=uuid4(), filename=filename)
    filepath = os.path.join(tempfile.gettempdir(), filename)

    with open(filepath, 'w') as temp_file:
        temp_file.write(content)

    return filepath


def copy_local_file_to_article(file_to_handle, file_name, article, owner, label=None, description=None,
                               replace=None, galley=False):
    """Copy a local file into an article's folder with appropriate mime type and permissions.

    :param file_to_handle: the uploaded file object we need to handle
    :param file_name: the name of the file
    :param article: the article to which the file belongs
    :param owner: the owner of the file
    :param label: the file's label (or title)
    :param description: the description of the item
    :param replace: the file to which this is a revision or None
    :return: a File object that has been saved in the database
    """

    original_filename = str(file_name)

    # N.B. os.path.splitext[1] always returns the final file extension, even in a multi-dotted (.txt.html etc.) input
    filename = str(uuid4()) + str(os.path.splitext(original_filename)[1])
    folder_structure = os.path.join(settings.BASE_DIR, 'files', 'articles', str(article.id))

    copy_file_to_folder(file_to_handle, filename, folder_structure)

    file_mime = guess_mime(filename)

    from core import models
    new_file = models.File(
        mime_type=file_mime,
        original_filename=original_filename,
        uuid_filename=filename,
        label=label,
        description=description,
        owner=owner,
        is_galley=galley,
        article_id=article.pk
    )

    new_file.save()

    return new_file


def save_file_to_article(file_to_handle, article, owner, label=None, description=None, replace=None, is_galley=False,
                         save=True):
    """Save a file into an article's folder with appropriate mime type and permissions.

    :param file_to_handle: the uploaded file object we need to handle
    :param article: the article to which the file belongs
    :param owner: the owner of the file
    :param label: the file's label (or title)
    :param description: the description of the item
    :param replace: the file to which this is a revision or None
    :return: a File object that has been saved in the database
    """

    if isinstance(file_to_handle, str):
        original_filename = os.path.basename(file_to_handle)
    else:
        original_filename = str(file_to_handle.name)

    print(original_filename)

    # N.B. os.path.splitext[1] always returns the final file extension, even in a multi-dotted (.txt.html etc.) input
    filename = str(uuid4()) + str(os.path.splitext(original_filename)[1])
    folder_structure = os.path.join(settings.BASE_DIR, 'files', 'articles', str(article.id))

    if save:
        save_file_to_disk(file_to_handle, filename, folder_structure)
        file_mime = file_path_mime(os.path.join(folder_structure, filename))
    else:
        os.rename(os.path.join(folder_structure, original_filename), os.path.join(folder_structure, filename))
        file_mime = guess_mime(filename)

    from core import models
    new_file = models.File(
        mime_type=file_mime,
        original_filename=original_filename,
        uuid_filename=filename,
        label=label,
        description=description,
        owner=owner,
        is_galley=is_galley,
        article_id=article.pk
    )

    new_file.save()

    return new_file


def guess_mime(filename):
    """ Attempt to ascertain the MIME type of a file

    :param filename: the filename of which to guess the type
    :return: the MIME type
    """
    file_mime = mime.guess_type(filename)

    try:
        file_mime = file_mime[0]
    except IndexError:
        file_mime = 'unknown'
    if not file_mime:
        file_mime = 'unknown'

    return file_mime


def file_path_mime(file_path):
    mime = magic.from_file(file_path, mime=True)
    return mime


def check_in_memory_mime(in_memory_file):
    mime = magic.from_buffer(in_memory_file.read(), mime=True)
    return mime


def copy_file_to_folder(file_to_handle, filename, folder_structure):
    """ Copy a local file to the disk in the specified folder

    :param file_to_handle: the file itself
    :param filename: the filename to save as
    :param folder_structure: the folder structure
    :return: None
    """
    # create the folder structure
    if not os.path.exists(folder_structure):
        os.makedirs(folder_structure)

    path = os.path.join(folder_structure, str(filename))

    # write the file to disk
    import shutil
    shutil.copy(file_to_handle, path)


def copy_article_file(article_to_copy_from, file_to_copy, article_to_copy_to):
    """
    Copies an article file to another article location.
    :param file_to_copy: A file object
    :param article_to_copy_to: An Article object
    :return: None
    """
    copy_to_folder_structure = os.path.join(settings.BASE_DIR,
                                            'files',
                                            'articles',
                                            str(article_to_copy_to.id))
    file_path = os.path.join(copy_to_folder_structure, file_to_copy.uuid_filename)
    mkdirs(copy_to_folder_structure)
    shutil.copy(file_to_copy.get_file_path(article_to_copy_from), file_path)


def save_file_to_disk(file_to_handle, filename, folder_structure):
    """ Save a file to the disk in the specified folder

    :param file_to_handle: the file itself
    :param filename: the filename to save as
    :param folder_structure: the folder structure
    :return: None
    """
    # create the folder structure
    if not os.path.exists(folder_structure):
        os.makedirs(folder_structure)

    path = os.path.join(folder_structure, str(filename))

    # write the file to disk
    with open(path, 'wb') as fd:
        for chunk in file_to_handle.chunks():
            fd.write(chunk)


def get_file(file_to_get, article):
    """Returns the content of a file using standard python open procedures (no encoding).

    :param file_to_get: the file object to retrieve
    :param article: the associated article
    :return: the contents of the file
    """
    path = os.path.join(settings.BASE_DIR, 'files', 'articles', str(article.id), str(file_to_get.uuid_filename))

    if not os.path.isfile(path):
        return ""

    try:
        with open(path, 'r') as content_file:
            content = content_file.read()
            return content
    except UnicodeDecodeError:
        with open(path, 'r', encoding='utf-8') as content_file:
            content = content_file.read()
            return content


def render_xml(file_to_render, article, galley=None):
    """Renders JATS and TEI XML into HTML for inline article display.

    :param file_to_render: the file object to retrieve and render
    :param article: the associated article
    :param galley: optional galley param, used to bastardise the graphics
    :return: a transform of the file to HTML through the XSLT processor
    """

    path = os.path.join(settings.BASE_DIR, 'files', 'articles', str(article.id), str(file_to_render.uuid_filename))

    if not os.path.isfile(path):
        util_models.LogEntry.add_entry(types='Error',
                                       description='The required XML file for a transform {0} was not found'.format(path),
                                       level='Error', actor=None, target=article)
        return ""

    if article.journal.has_xslt:
        xsl_path = os.path.join(settings.BASE_DIR, 'files', 'journals', str(article.journal.id), 'journal.xslt')
    else:
        xsl_path = os.path.join(settings.BASE_DIR, 'transform', 'xsl', "article.xsl")

    if not os.path.isfile(xsl_path):
        util_models.LogEntry.add_entry(types='Error', description='The required XSLT file {0} was not found'.format(xsl_path),
                                       level='Error', actor=None, target=article)
        return ""

    with open(path, "rb") as xml_file_contents:
        xml = BeautifulSoup(xml_file_contents, "lxml-xml")

        transform = etree.XSLT(etree.parse(xsl_path))

        # remove the <?xml version="1.0" encoding="utf-8"?> line (or similar) if it exists
        regex = re.compile(r'<\?xml version="1.0" encoding=".+"\?>')
        xml_string = str(xml)
        xml_string = re.sub(regex, '', xml_string, count=1)

        return transform(etree.XML(xml_string))


def serve_file(request, file_to_serve, article, public=False):
    """Serve a file to the user using a StreamingHttpResponse.

    :param request: the active request
    :param file_to_serve: the file object to retrieve and serve
    :param article: the associated article
    :param public: boolean
    :return: a StreamingHttpResponse object with the requested file or an HttpResponseRedirect if there is an IO or
    permission error
    """
    file_path = os.path.join(settings.BASE_DIR, 'files', 'articles', str(article.id), str(file_to_serve.uuid_filename))

    try:
        return serve_file_to_browser(file_path, file_to_serve, public=public)
    except IOError:
        messages.add_message(request, messages.ERROR, 'File not found. {0}'.format(file_path))
        raise Http404


@cache_control(max_age=600)
def serve_file_to_browser(file_path, file_to_serve, public=False):
    """ Stream a file to the browser in a safe way

    :param file_path: the path on disk to the file
    :param file_to_serve: the core.models.File object to serve
    :param public: boolean
    :return: HttpStreamingResponse object
    """
    # stream the response to the browser
    # we use the UUID filename to avoid any security risks of putting user content in headers
    # we set a chunk size of 8192 so that the entire file isn't loaded into memory if it's large
    filename, extension = os.path.splitext(file_to_serve.original_filename)

    if file_to_serve.mime_type in IMAGE_MIMETYPES:
        response = HttpResponse(FileWrapper(open(file_path, 'rb'), 8192), content_type=file_to_serve.mime_type)
    else:
        response = StreamingHttpResponse(FileWrapper(open(file_path, 'rb'), 8192), content_type=file_to_serve.mime_type)

    response['Content-Length'] = os.path.getsize(file_path)
    if public:
        response['Content-Disposition'] = 'attachment; filename="{0}"'.format(file_to_serve.public_download_name())
    else:
        response['Content-Disposition'] = 'attachment; filename="{0}{1}"'.format(slugify(filename), extension)

    return response


def delete_file(article_object, file_object):
    """ Deletes a file. Note: the actual file is not deleted, this just removes the association of the file with an
    article.

    :param article_object: the article associated with the file
    :param file_object: the file object to delete
    :return: None
    """
    if article_object.manuscript_files.filter(id=file_object.id).exists():
        article_object.manuscript_files.remove(file_object)
    else:
        article_object.data_figure_files.remove(file_object)


def replace_file(article_to_replace, file_to_replace, new_file, copyedit=None, galley=None):
    """ Replaces an existing file with a new record

    :param article_to_replace: the article in which we replace the file
    :param file_to_replace: the file to be replaces
    :param new_file: the new file model
    :param copyedit: a CopyeditAssignment object
    :return: the new file model object
    """
    from core import models
    if not copyedit:
        if file_to_replace in article_to_replace.manuscript_files.all():
            article_to_replace.manuscript_files.remove(file_to_replace)

            # reload the new file to avoid conflicts with raw SQL due to materialized path tree structure
            new_file = get_object_or_404(models.File, pk=new_file.pk)
            new_file.label = file_to_replace.label
            new_file.parent = file_to_replace
            new_file.save()
            article_to_replace.manuscript_files.add(new_file)

        elif file_to_replace in article_to_replace.data_figure_files.all():
            article_to_replace.data_figure_files.remove(file_to_replace)

            # reload the new file to avoid conflicts with raw SQL due to materialized path tree structure
            new_file = get_object_or_404(models.File, pk=new_file.pk)
            new_file.label = file_to_replace.label
            new_file.parent = file_to_replace
            new_file.save()
            article_to_replace.data_figure_files.add(new_file)

        else:
            new_file = get_object_or_404(models.File, pk=new_file.pk)
            new_file.label = file_to_replace.label
            new_file.parent = file_to_replace
            new_file.save()
    else:
        if file_to_replace in copyedit.copyeditor_files.all():
            copyedit.copyeditor_files.remove(file_to_replace)

            # reload the new file to avoid conflicts with raw SQL du to materialized path tree structure
            new_file = get_object_or_404(models.File, pk=new_file.pk)
            new_file.parent = file_to_replace
            new_file.save()
            copyedit.copyeditor_files.add(new_file)

    return new_file


def create_file_history_object(file_to_replace):
    file_history_dict = {
        'mime_type': file_to_replace.mime_type,
        'original_filename': file_to_replace.original_filename,
        'uuid_filename': file_to_replace.uuid_filename,
        'label': file_to_replace.label,
        'description': file_to_replace.description,
        'sequence': file_to_replace.sequence,
        'owner': file_to_replace.owner,
        'privacy': file_to_replace.privacy,
        'history_seq': file_to_replace.next_history_seq()
    }

    from core import models
    new_file = models.FileHistory.objects.create(**file_history_dict)

    file_to_replace.history.add(new_file)


def overwrite_file(uploaded_file, article, file_to_replace):

    create_file_history_object(file_to_replace)
    original_filename = str(uploaded_file.name)

    # N.B. os.path.splitext[1] always returns the final file extension, even in a multi-dotted (.txt.html etc.) input
    filename = str(uuid4()) + str(os.path.splitext(original_filename)[1])
    folder_structure = os.path.join(settings.BASE_DIR, 'files', 'articles', str(article.id))

    save_file_to_disk(uploaded_file, filename, folder_structure)

    file_to_replace.uuid_filename = filename
    file_to_replace.original_filename = original_filename
    file_to_replace.mime_type = guess_mime(filename)

    file_to_replace.save()

    return file_to_replace


def reinstate_historic_file(article, current_file, file_history):
    create_file_history_object(current_file)

    file_history_dict = {
        'mime_type': file_history.mime_type,
        'original_filename': file_history.original_filename,
        'uuid_filename': file_history.uuid_filename,
        'label': file_history.label,
        'description': file_history.description,
        'sequence': file_history.sequence,
        'owner': file_history.owner,
        'privacy': file_history.privacy,
        'history_seq': file_history.history_seq
    }

    for attr, value in file_history_dict.items():
        setattr(current_file, attr, value)

    current_file.save()


def serve_journal_cover(request, file_to_serve):
    """Serve a file to the user using a StreamingHttpResponse.

    :param request: the active request
    :param file_to_serve: the file object to retrieve and serve
    :return: a StreamingHttpResponse object with the requested file or an HttpResponseRedirect if there is an IO or
    permission error
    """

    file_path = os.path.join(settings.BASE_DIR, 'files', 'journals', str(request.journal.id),
                             str(file_to_serve.uuid_filename))

    try:
        response = serve_file_to_browser(file_path, file_to_serve)
        return response
    except IOError:
        messages.add_message(request, messages.ERROR, 'File not found. {0}'.format(file_path))
        return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


def serve_press_cover(request, file_to_serve):
    """Serve a file to the user using a StreamingHttpResponse.

    :param request: the active request
    :param file_to_serve: the file to serve
    :return: a StreamingHttpResponse object with the requested file or an HttpResponseRedirect if there is an IO or
    permission error
    """

    file_path = os.path.join(settings.BASE_DIR, 'files', 'press', str(file_to_serve.uuid_filename))

    try:
        response = serve_file_to_browser(file_path, file_to_serve)
        return response
    except IOError:
        messages.add_message(request, messages.ERROR, 'File not found. {0}'.format(file_path))
        return HttpResponseRedirect(request.META.get('HTTP_REFERER'))


def save_file_to_journal(request, file_to_handle, label, description, xslt=False, public=False):
    original_filename = str(file_to_handle.name)

    # N.B. os.path.splitext[1] always returns the final file extension, even in a multi-dotted (.txt.html etc.) input
    if xslt:
        filename = "journal.xslt"
    else:
        filename = str(uuid4()) + str(os.path.splitext(original_filename)[1])

    folder_structure = os.path.join(settings.BASE_DIR, 'files', 'journals', str(request.journal.id))

    save_file_to_disk(file_to_handle, filename, folder_structure)

    file_mime = guess_mime(filename)

    from core import models
    new_file = models.File.objects.create(
        mime_type=file_mime,
        original_filename=original_filename,
        uuid_filename=filename,
        label=label,
        description=description,
        owner=request.user,
        is_galley=False,
        privacy="public" if public else "owner"
    )

    return new_file


def unlink_journal_file(request, file=None, xslt=False):
    if xslt:
        filename = 'journal.xslt'
    else:
        filename = file.uuid_filename

    full_path = os.path.join(settings.BASE_DIR, 'files', 'journals', str(request.journal.id), filename)

    if os.path.isfile(full_path):
        os.unlink(full_path)


def save_file_to_press(request, file_to_handle, label, description, public=False):
    original_filename = str(file_to_handle.name)

    # N.B. os.path.splitext[1] always returns the final file extension, even in a multi-dotted (.txt.html etc.) input
    filename = str(uuid4()) + str(os.path.splitext(original_filename)[1])
    folder_structure = os.path.join(settings.BASE_DIR, 'files', 'press')

    save_file_to_disk(file_to_handle, filename, folder_structure)

    file_mime = guess_mime(filename)

    from core import models
    new_file = models.File.objects.create(
        mime_type=file_mime,
        original_filename=original_filename,
        uuid_filename=filename,
        label=label,
        description=description,
        owner=request.user,
        is_galley=False,
        privacy="public" if public else "owner"
    )

    return new_file


def save_file_to_temp(file_to_handle):
    original_filename = str(file_to_handle.name)
    filename = str(uuid4()) + str(os.path.splitext(original_filename)[1])
    folder_structure = os.path.join(settings.BASE_DIR, 'files', 'temp')
    save_file_to_disk(file_to_handle, filename, folder_structure)

    return [filename, os.path.join(folder_structure, filename)]


def get_temp_file_path_from_name(filename):
    return os.path.join(settings.BASE_DIR, 'files', 'temp', filename)


def file_parents(file):
    """
    Returns an ordered list of file parents
    :param file: a File object
    :return: a list of File objects
    """
    if not file.parent:
        return []

    if file.parent:
        files = []
        parent = file.parent

        while parent:
            files.append(parent)
            if parent.parent:
                parent = parent
            else:
                parent = None

        return files


def file_children(file):
    from core import models
    children = models.File.objects.filter(parent=file)
    return children


def zip_files(files):
    file_name = '{0}.zip'.format(uuid4())

    # Copy files into a temp dir
    _dir = os.path.join(settings.BASE_DIR, 'files/temp', str(uuid4()))
    os.makedirs(_dir, 0o775)

    for file in files:
        shutil.copy(file.self_article_path(), _dir)

    zip_path = '{dir}.zip'.format(dir=_dir)

    shutil.make_archive(_dir, 'zip', _dir)
    shutil.rmtree(_dir)
    return zip_path, file_name


def serve_temp_file(file_path, file_name):
    filename, extension = os.path.splitext(file_name)
    mime_type = guess_mime(file_name)

    response = StreamingHttpResponse(FileWrapper(open(file_path, 'rb'), 8192), content_type=mime_type)

    response['Content-Length'] = os.path.getsize(file_path)
    response['Content-Disposition'] = 'attachment; filename="{0}{1}"'.format(slugify(filename), extension)

    unlink_temp_file(file_path)

    return response


def unlink_temp_file(file_path):
    if os.path.isfile(file_path):
        os.unlink(file_path)


def checksum(file_path):
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()
