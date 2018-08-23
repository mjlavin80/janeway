__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"
from django.db import models
from django.utils import timezone


class ProofingAssignment(models.Model):
    article = models.OneToOneField('submission.Article')
    proofing_manager = models.ForeignKey('core.Account', null=True, on_delete=models.SET_NULL)
    editor = models.ForeignKey('core.Account', null=True, related_name='proofing_editor')
    assigned = models.DateTimeField(default=timezone.now)
    notified = models.BooleanField(default=False)
    completed = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ('article', 'proofing_manager')

    @property
    def current_proofing_round_number(self):
        try:
            return self.proofinground_set.all().order_by('-number')[0].number
        except IndexError:
            return 0

    def current_proofing_round(self):
        try:
            return self.proofinground_set.all().order_by('-number')[0]
        except IndexError:
            return None

    def add_new_proofing_round(self):
        new_round_number = self.current_proofing_round_number + 1
        return ProofingRound.objects.create(assignment=self,
                                            number=new_round_number)

    def user_is_manager(self, user):
        if user == self.proofing_manager:
            return True
        return False

    def __str__(self):
        return 'Proofing Assignment {pk}'.format(pk=self.pk)


class ProofingRound(models.Model):
    assignment = models.ForeignKey(ProofingAssignment)
    number = models.PositiveIntegerField(default=1)
    date_started = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-number',)

    def __str__(self):
        return "Round #{0} for Article {1}".format(self.number, self.assignment.article.title)

    @property
    def has_active_tasks(self):
        if self.proofingtask_set.filter(completed__isnull=True):
            return True
        else:
            return False

    @property
    def active_proofreaders(self):
        return [task.proofreader for task in self.proofingtask_set.all()]

    @property
    def typeset_tasks(self):
        typeset_tasks = list()
        for p_task in self.proofingtask_set.all():
            for t_task in p_task.typesetterproofingtask_set.all():
                typeset_tasks.append(t_task)

        return typeset_tasks


class ProofingTask(models.Model):
    round = models.ForeignKey(ProofingRound)
    proofreader = models.ForeignKey('core.Account', null=True, on_delete=models.SET_NULL)
    assigned = models.DateTimeField(default=timezone.now)
    notified = models.BooleanField(default=False)
    due = models.DateTimeField(default=None, verbose_name="Date Due")
    accepted = models.DateTimeField(blank=True, null=True)
    completed = models.DateTimeField(blank=True, null=True)
    cancelled = models.BooleanField(default=False)
    acknowledged = models.DateTimeField(blank=True, null=True)

    task = models.TextField(verbose_name="Proofing Task")
    galleys_for_proofing = models.ManyToManyField('core.Galley')
    proofed_files = models.ManyToManyField('core.File')
    notes = models.ManyToManyField('proofing.Note')

    def __str__(self):
        return "{0} proofing {1} in round {2}".format(self.proofreader.full_name(),
                                                      self.round.assignment.article.title,
                                                      self.round.number)

    @property
    def assignment(self):
        return self.round.assignment

    def typesetter_tasks(self):
        return self.typesetterproofingtask_set.all()

    def status(self):
        if self.cancelled:
            return {'slug': 'cancelled', 'friendly': 'Task cancelled'}
        elif self.assigned and not self.accepted and not self.completed:
            return {'slug': 'assigned', 'friendly': 'Awaiting response'}
        elif self.assigned and self.accepted and not self.completed:
            return {'slug': 'accepted', 'friendly': 'Task accepted, underway'}
        elif self.assigned and not self.accepted and self.completed:
            return {'slug': 'declined', 'friendly': 'Task declined'}
        elif self.completed:
            return {'slug': 'completed', 'friendly': 'Task completed'}

    def galley_files(self):
        return [galley.file for galley in self.galleys_for_proofing.all()]

    def actor(self):
        return self.proofreader

    def review_comments(self):
        comment_text = ''
        for note in self.notes.all().order_by('galley'):
            comment_text = comment_text + "Comment by: {0} for Galley {1}<br>{2}<br>".format(note.creator.full_name(),
                                                                                             note.galley,
                                                                                             note.text)

        return comment_text


class TypesetterProofingTask(models.Model):
    proofing_task = models.ForeignKey(ProofingTask)
    typesetter = models.ForeignKey('core.Account', null=True, on_delete=models.SET_NULL)
    assigned = models.DateTimeField(default=timezone.now)
    notified = models.BooleanField(default=False)
    due = models.DateTimeField(blank=True, null=True)
    accepted = models.DateTimeField(blank=True, null=True)
    completed = models.DateTimeField(blank=True, null=True)
    cancelled = models.BooleanField(default=False)
    acknowledged = models.DateTimeField(blank=True, null=True)

    task = models.TextField(verbose_name="Typesetter Task")
    galleys = models.ManyToManyField('core.Galley')
    files = models.ManyToManyField('core.File')
    notes = models.TextField(verbose_name="Correction Note", blank=True, null=True)

    class Meta:
        verbose_name = 'Correction Task'

    def __str__(self):
        return "Correction Task Proof ID: {0}, Proofreader {1}, Due: {2}".format(self.proofing_task.pk,
                                                                                 self.typesetter.full_name(),
                                                                                 self.due)

    def status(self):
        if self.cancelled:
            return {'slug': 'cancelled', 'friendly': 'Cancelled'}
        elif self.assigned and not self.accepted and not self.completed:
            return {'slug': 'assigned', 'friendly': 'Awaiting response'}
        elif self.assigned and self.accepted and not self.completed:
            return {'slug': 'accepted', 'friendly': 'Underway'}
        elif self.assigned and not self.accepted and self.completed:
            return {'slug': 'declined', 'friendly': 'Declined'}
        elif self.completed:
            return {'slug': 'completed', 'friendly': 'Completed'}

    def actor(self):
        return self.typesetter


class Note(models.Model):
    galley = models.ForeignKey('core.Galley')
    creator = models.ForeignKey('core.Account', related_name='proofing_note_creator',
                                null=True, on_delete=models.SET_NULL)
    text = models.TextField()
    date_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-date_time',)

    def __str__(self):
        return "{0} - {1} {2}".format(self.pk, self.creator.full_name(), self.galley)
