__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"
import json
import os
import codecs

from django.conf import settings

from core import models as core_models
from journal import models
from press import models as press_models
from utils import setting_handler
from submission import models as submission_models


def update_settings(journal_object, management_command=False, overwrite_with_defaults=False):
    """ Updates or creates the settings for a journal from journal_defaults.json.

    :param journal_object: the journal object to update
    :param management_command: whether or not to print output to the console
    :return: None
    """
    with codecs.open(os.path.join(settings.BASE_DIR, 'utils/install/journal_defaults.json'), encoding='utf-8') as json_data:

        default_data = json.load(json_data)

        for item in default_data:
            setting_group, created = core_models.SettingGroup.objects.get_or_create(
                name=item['group'].get('name'),
            )

            setting_defaults = {
                'types': item['setting'].get('type'),
                'pretty_name': item['setting'].get('pretty_name'),
                'description': item['setting'].get('description'),
                'is_translatable': item['setting'].get('is_translatable')
            }

            setting, created = core_models.Setting.objects.get_or_create(
                name=item['setting'].get('name'),
                group=setting_group,
                defaults=setting_defaults
            )

            if not created:
                for k, v in setting_defaults.items():
                    if not getattr(setting, k) == v:
                        setattr(setting, k, v)
                        setting.save()

            setting_value, created = core_models.SettingValue.objects.language('en').get_or_create(
                journal=journal_object,
                setting=setting
            )

            if created or overwrite_with_defaults:
                setting_value.value = item['value'].get('default')
                setting_value.save()

            if management_command:
                print('Parsed setting {0}'.format(item['setting'].get('name')))


def update_emails(journal_object, management_command=False):
    """
    Updates email settings with new versions.
    :param journal_object: Journal object
    :param management_command: Boolean
    :return: Nothing
    """
    with codecs.open(os.path.join(settings.BASE_DIR, 'utils/install/journal_defaults.json'), encoding='utf-8') as json_data:

        default_data = json.load(json_data)

        for item in default_data:
            group_name = item['group'].get('name')

            if group_name == 'email':

                setting_defaults = {
                    'types': item['setting'].get('type'),
                    'pretty_name': item['setting'].get('pretty_name'),
                    'description': item['setting'].get('description'),
                    'is_translatable': item['setting'].get('is_translatable')
                }

                setting, created = core_models.Setting.objects.get_or_create(
                    name=item['setting'].get('name'),
                    group__name=group_name,
                    defaults=setting_defaults
                )

                setting_value, created = core_models.SettingValue.objects.language('en').get_or_create(
                    journal=journal_object,
                    setting=setting
                )

                setting_value.value = item['value'].get('default')
                setting_value.save()

                if management_command:
                    print('{0} Updated'.format(setting.name))


def update_license(journal_object, management_command=False):
    """ Updates or creates the settings for a journal from journal_defaults.json.

    :param journal_object: the journal object to update
    :param management_command: whether or not to print output to the console
    :return: None
    """
    with codecs.open(os.path.join(settings.BASE_DIR, 'utils/install/licence.json'), encoding='utf-8') as json_data:

        default_data = json.load(json_data)

        for item in default_data:
            default_dict = {
                'name': item['fields'].get('name'),
                'url': item['fields'].get('url'),
                'text': item['fields'].get('text'),
            }
            licence, created = submission_models.Licence.objects.get_or_create(
                journal=journal_object,
                short_name=item['fields'].get('short_name'),
                defaults=default_dict
            )

            if management_command:
                print('Parsed licence {0}'.format(item['fields'].get('short_name')))


def journal(name, code, base_url, delete):
    """ Installs a journal into the system.

    :param name: the name of the new journal
    :param code: the journal's short codename
    :param base_url: the full sub domain at which the journal will reside. E.g. sub domain.domain.org
    :param delete: if true, deletes the journal if it exists
    :return: None
    """

    if delete:
        try:
            models.Journal.objects.get(code=code, domain=base_url).delete()
        except models.Journal.DoesNotExist:
            print('Journal not found, nothing to delete')

    journal_object = models.Journal.objects.create(code=code, domain=base_url)
    update_settings(journal_object, management_command=True)
    setting_handler.save_setting('general', 'journal_name', journal_object, name)
    journal_object.setup_directory()


def press(name, code, domain):
    """ Install the press. Each Janeway instance can host one press with an indefinite number of journals.

    :param name: the name of the press
    :param code: the press's short codename
    :param domain: the domain at which the press resides
    :return: None
    """
    press_models.Press.objects.create(name=name, code=code, domain=domain)
