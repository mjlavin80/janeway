__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"


import datetime

from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse
from django.core.management import call_command


from core import models as core_models
from journal import models as journal_models
from production import models as production_models
from review import models as review_models
from submission import models as submission_models
from proofing import models as proofing_models
from press import models as press_models


# Create your tests here.
class ReviewTests(TestCase):

    def test_index_view_with_no_questions(self):
        """
        If no questions exist, an appropriate message should be displayed.
        """
        response = self.client.get(reverse('website_index'))
        self.assertEqual(response.status_code, 200)

    @staticmethod
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

    @staticmethod
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

    @staticmethod
    def create_journals():
        """
        Creates a set of dummy journals for testing
        :return: a 2-tuple of two journals
        """
        journal_one = journal_models.Journal(code="TST", domain="testserver")
        journal_one.save()

        journal_two = journal_models.Journal(code="TSA", domain="journal2.localhost")
        journal_two.save()

        return journal_one, journal_two

    def setUp(self):
        """
        Setup the test environment.
        :return: None
        """
        self.journal_one, self.journal_two = self.create_journals()
        self.create_roles(["editor", "author", "reviewer", "proofreader", "production", "copyeditor", "typesetter",
                           "proofing_manager", "section-editor"])

        self.regular_user = self.create_user("regularuser@martineve.com")
        self.regular_user.is_active = True
        self.regular_user.save()

        self.second_user = self.create_user("seconduser@martineve.com", ["reviewer"], journal=self.journal_one)
        self.second_user.is_active = True
        self.second_user.save()

        self.admin_user = self.create_user("adminuser@martineve.com")
        self.admin_user.is_staff = True
        self.admin_user.is_active = True
        self.admin_user.save()

        self.inactive_user = self.create_user("disableduser@martineve.com", ["editor", "author", "proofreader",
                                                                             "production"], journal=self.journal_one)
        self.inactive_user.is_active = False
        self.inactive_user.save()

        self.editor = self.create_user("editoruser@martineve.com", ["editor"], journal=self.journal_one)
        self.editor.is_active = True
        self.editor.save()

        self.author = self.create_user("authoruser@martineve.com", ["author"], journal=self.journal_one)
        self.author.is_active = True
        self.author.save()

        self.proofreader = self.create_user("proofreader@martineve.com", ["proofreader"], journal=self.journal_one)
        self.proofreader.is_active = True
        self.proofreader.save()

        self.proofreader_two = self.create_user("proofreader2@martineve.com", ["proofreader"], journal=self.journal_one)
        self.proofreader_two.is_active = True
        self.proofreader_two.save()

        self.production = self.create_user("production@martineve.com", ["production"], journal=self.journal_one)
        self.production.is_active = True
        self.production.save()

        self.copyeditor = self.create_user("copyeditor@martineve.com", ["copyeditor"], journal=self.journal_one)
        self.copyeditor.is_active = True
        self.copyeditor.save()

        self.typesetter = self.create_user("typesetter@martineve.com", ["typesetter"], journal=self.journal_one)
        self.typesetter.is_active = True
        self.typesetter.save()

        self.other_typesetter = self.create_user("other_typesetter@martineve.com", ["typesetter"],
                                                 journal=self.journal_one)
        self.other_typesetter.is_active = True
        self.other_typesetter.save()

        self.proofing_manager = self.create_user("proofing_manager@martineve.com", ["proofing_manager"],
                                                 journal=self.journal_one)
        self.proofing_manager.is_active = True
        self.proofing_manager.save()

        self.other_typesetter.is_active = True
        self.other_typesetter.save()

        self.section_editor = self.create_user("section_editor@martineve.com", ['section-editor'],
                                               journal=self.journal_one)
        self.section_editor.is_active = True
        self.section_editor.save()

        self.second_reviewer = self.create_user("second_reviewer@martineve.com", ['reviewer'],
                                                journal=self.journal_one)
        self.second_reviewer.is_active = True
        self.second_reviewer.save()

        self.public_file = core_models.File(mime_type="A/FILE",
                                            original_filename="blah.txt",
                                            uuid_filename="UUID.txt",
                                            label="A file that is public",
                                            description="Oh yes, it's a file",
                                            owner=self.regular_user,
                                            is_galley=False,
                                            privacy="public")

        self.public_file.save()

        self.private_file = core_models.File(mime_type="A/FILE",
                                             original_filename="blah.txt",
                                             uuid_filename="UUID.txt",
                                             label="A file that is private",
                                             description="Oh yes, it's a file",
                                             owner=self.regular_user,
                                             is_galley=False,
                                             privacy="owner")

        self.private_file.save()

        self.article_in_production = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                               abstract="An abstract",
                                                               stage=submission_models.STAGE_TYPESETTING,
                                                               journal_id=self.journal_one.id)
        self.article_in_production.save()
        self.article_in_production.data_figure_files.add(self.public_file)

        self.article_unsubmitted = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                             abstract="An abstract",
                                                             stage=submission_models.STAGE_UNSUBMITTED,
                                                             journal_id=self.journal_one.id)
        self.article_unsubmitted.save()

        self.article_unassigned = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                            abstract="An abstract",
                                                            stage=submission_models.STAGE_UNASSIGNED,
                                                            journal_id=self.journal_one.id)
        self.article_unassigned.save()

        self.article_assigned = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                          abstract="An abstract",
                                                          stage=submission_models.STAGE_ASSIGNED,
                                                          journal_id=self.journal_one.id)
        self.article_assigned.save()

        self.article_under_review = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                              abstract="An abstract",
                                                              stage=submission_models.STAGE_UNDER_REVIEW,
                                                              journal_id=self.journal_one.id)
        self.article_under_review.save()

        self.article_review_completed = submission_models.Article.objects.create(owner=self.regular_user,
                                                                                 title="A Test Article",
                                                                                 abstract="An abstract",
                                                                                 stage=submission_models.STAGE_ACCEPTED,
                                                                                 journal_id=self.journal_one.id,
                                                                                 date_accepted=timezone.now())

        self.article_author_is_owner = submission_models.Article.objects.create(owner=self.author,
                                                                                title="A Test Article",
                                                                                abstract="An abstract",
                                                                                stage=submission_models.STAGE_ACCEPTED,
                                                                                journal_id=self.journal_one.id,
                                                                                date_accepted=timezone.now())

        self.article_author_is_owner.authors.add(self.editor)
        self.article_author_is_owner.authors.add(self.author)

        self.review_form = review_models.ReviewForm(name="A Form", slug="A Slug", intro="i", thanks="t",
                                                    journal=self.journal_one)
        self.review_form.save()

        self.review_assignment_complete = review_models.ReviewAssignment(article=self.article_review_completed,
                                                                         reviewer=self.regular_user,
                                                                         editor=self.editor,
                                                                         date_due=datetime.datetime.now(),
                                                                         form=self.review_form,
                                                                         is_complete=True,
                                                                         date_complete=timezone.now())

        self.review_assignment_complete.save()

        self.review_assignment = review_models.ReviewAssignment(article=self.article_under_review,
                                                                reviewer=self.second_user,
                                                                editor=self.editor,
                                                                date_due=datetime.datetime.now(),
                                                                form=self.review_form)

        self.review_assignment.save()

        self.review_assignment_not_in_scope = review_models.ReviewAssignment(article=self.article_in_production,
                                                                             reviewer=self.regular_user,
                                                                             editor=self.editor,
                                                                             date_due=datetime.datetime.now(),
                                                                             form=self.review_form)
        self.review_assignment_not_in_scope.save()

        self.article_under_revision = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                                abstract="An abstract",
                                                                stage=submission_models.STAGE_UNDER_REVISION,
                                                                journal_id=self.journal_one.id)
        self.article_under_revision.save()

        self.article_rejected = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                          abstract="An abstract",
                                                          stage=submission_models.STAGE_REJECTED,
                                                          journal_id=self.journal_one.id)
        self.article_rejected.save()

        self.article_accepted = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                          abstract="An abstract",
                                                          stage=submission_models.STAGE_ACCEPTED,
                                                          journal_id=self.journal_one.id)
        self.article_accepted.save()

        self.section_editor_assignment = review_models.EditorAssignment(article=self.article_assigned,
                                                                        editor=self.section_editor,
                                                                        editor_type='section-editor',
                                                                        notified=True)
        self.section_editor_assignment.save()

        self.article_editor_copyediting = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                                    abstract="An abstract",
                                                                    stage=submission_models.STAGE_EDITOR_COPYEDITING,
                                                                    journal_id=self.journal_one.id)
        self.article_editor_copyediting.save()

        self.article_author_copyediting = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                                    abstract="An abstract",
                                                                    stage=submission_models.STAGE_AUTHOR_COPYEDITING,
                                                                    journal_id=self.journal_one.id)
        self.article_author_copyediting.save()

        self.article_final_copyediting = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                                   abstract="An abstract",
                                                                   stage=submission_models.STAGE_FINAL_COPYEDITING,
                                                                   journal_id=self.journal_one.id)
        self.article_final_copyediting.save()

        self.article_proofing = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                          abstract="An abstract",
                                                          stage=submission_models.STAGE_PROOFING,
                                                          journal_id=self.journal_one.id)
        self.article_proofing.save()

        assigned = production_models.ProductionAssignment(article=self.article_in_production,
                                                          production_manager=self.production)
        assigned.save()

        self.article_published = submission_models.Article(owner=self.regular_user, title="A Second Test Article",
                                                           abstract="An abstract",
                                                           stage=submission_models.STAGE_PUBLISHED,
                                                           journal_id=self.journal_one.id)
        self.article_published.save()

        assigned = production_models.ProductionAssignment(article=self.article_published,
                                                          production_manager=self.production)
        assigned.save()

        self.article_in_production_inactive = submission_models.Article(owner=self.regular_user, title="A Test Article",
                                                                        abstract="An abstract",
                                                                        stage=submission_models.STAGE_TYPESETTING,
                                                                        journal_id=self.journal_one.id)
        self.article_in_production_inactive.save()

        self.assigned = production_models.ProductionAssignment(article=self.article_in_production_inactive,
                                                               production_manager=self.inactive_user)
        self.assigned.save()

        self.typeset_task = production_models.TypesetTask(assignment=self.assigned,
                                                          typesetter=self.typesetter,
                                                          notified=True,
                                                          accepted=timezone.now())
        self.typeset_task.save()

        self.other_typeset_task = production_models.TypesetTask(assignment=self.assigned,
                                                                typesetter=self.other_typesetter,
                                                                notified=True,
                                                                accepted=timezone.now())
        self.other_typeset_task.save()

        self.proofing_assignment = proofing_models.ProofingAssignment(article=self.article_proofing,
                                                                      proofing_manager=self.proofing_manager,
                                                                      notified=True)
        self.proofing_assignment.save()
        self.proofing_assignment.add_new_proofing_round()

        self.proofing_task = proofing_models.ProofingTask(round=self.proofing_assignment.current_proofing_round(),
                                                          proofreader=self.proofreader,
                                                          notified=True,
                                                          due=timezone.now(),
                                                          accepted=timezone.now(),
                                                          task='sdfsdffs')
        self.proofing_task.save()

        self.correction_task = proofing_models.TypesetterProofingTask(proofing_task=self.proofing_task,
                                                                      typesetter=self.typesetter,
                                                                      notified=True,
                                                                      due=timezone.now(),
                                                                      accepted=timezone.now(),
                                                                      task='fsddsff')
        self.correction_task.save()

        call_command('sync_settings_to_journals')
        self.journal_one.name = 'Journal One'
        self.journal_two.name = 'Journal Two'
        self.press = press_models.Press.objects.create(name='Press', domain='localhost', main_contact='a@b.com')
        self.press.save()
        call_command('sync_journals_to_sites')
