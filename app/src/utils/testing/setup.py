__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"

from django.utils.six import StringIO
import sys

from django.core.management import call_command

from core import models as core_models
from journal import models as journal_models
from press import models as press_models


def create_user(username, roles=None, journal=None):
    """
    Creates a user with the specified permissions.
    :return: a user with the specified permissions
    """
    # check this way to avoid mutable default argument
    if roles is None:
        roles = []

    kwargs = {'username': username}
    user = core_models.Account.objects.create_user(email=username, **kwargs)

    for role in roles:
        resolved_role = core_models.Role.objects.get(name=role)
        core_models.AccountRole(user=user, role=resolved_role, journal=journal).save()

    user.save()

    return user


def create_roles(roles=None):
    """
    Creates the necessary roles for testing.
    :return: None
    """
    # check this way to avoid mutable default argument
    if roles is None:
        roles = []

    for role in roles:
        core_models.Role(name=role, slug=role).save()


def create_journals():
    """
    Creates a set of dummy journals for testing
    :return: a 2-tuple of two journals
    """
    journal_one = journal_models.Journal(code="TST", domain="testserver")
    journal_one.save()

    journal_two = journal_models.Journal(code="TSA", domain="journal2.localhost")
    journal_two.save()

    out = StringIO()
    sys.stdout = out

    call_command('sync_settings_to_journals', stdout=out)
    call_command('sync_journals_to_sites', stdout=out)

    journal_one.name = 'Journal One'
    journal_two.name = 'Journal Two'

    return journal_one, journal_two


def create_press():
    return press_models.Press.objects.create(name='Press', domain='localhost', main_contact='a@b.com')


def create_regular_user():
    regular_user = create_user("regularuser@martineve.com")
    regular_user.is_active = True
    regular_user.save()
    return regular_user


def create_second_user(journal):
    second_user = create_user("seconduser@martineve.com", ["reviewer"], journal=journal)
    second_user.is_active = True
    second_user.save()
    return second_user


def create_editor(journal):
    editor = create_user("editoruser@martineve.com", ["editor"], journal=journal)
    editor.is_active = True
    editor.save()
    return editor


def create_author(journal):
    author = create_user("authoruser@martineve.com", ["author"], journal=journal)
    author.is_active = True
    author.save()
    return author


class Request(object):
    """
    A fake request class for sending emails outside of the client-server request loop.
    """

    def __init__(self):
        self.journal = None
        self.site_type = None
        self.port = 8000
        self.secure = False
        self.user = False
        self.FILES = None
        self.META = {'REMOTE_ADDR': '127.0.0.1'}
        self.model_content_type = None

    def is_secure(self):
        if self.secure is False:
            return False
        else:
            return True

    def get_host(self):
        return 'testserver'
