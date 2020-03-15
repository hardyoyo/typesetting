from django.shortcuts import render, get_object_or_404, redirect, reverse
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.core.exceptions import PermissionDenied

from plugins.typesetting import plugin_settings, models, logic, forms, security
from security import decorators
from submission import models as submission_models
from core import models as core_models, files
from production import logic as production_logic
from journal.views import article_figure
from utils import models as utils_models


@decorators.has_journal
@decorators.editor_user_required
def typesetting_manager(request):
    pass


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_articles(request):
    """
    Displays a list of articles in the Typesetting stage.
    :param request: HttpRequest
    :return: HttpResponse
    """
    article_filter = request.GET.get('filter', None)

    articles_in_typesetting = submission_models.Article.objects.filter(
        stage=plugin_settings.STAGE,
    )

    if article_filter and article_filter == 'me':
        articles_in_typesetting = articles_in_typesetting.filter(
            typesettingclaim__editor=request.user,
            journal=request.journal,
        )

    template = 'typesetting/typesetting_articles.html'
    context = {
        'articles_in_typesetting': articles_in_typesetting,
        'filter': article_filter,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_article(request, article_id):
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )
    rounds = models.TypesettingRound.objects.filter(article=article)
    galleys = core_models.Galley.objects.filter(
        article=article,
    )
    manuscript_files = logic.production_ready_files(article)

    if not rounds:
        logic.new_typesetting_round(article, rounds, request.user)
        messages.add_message(
            request,
            messages.INFO,
            'New typesetting round created.',
        )

        return redirect(
            reverse(
                'typesetting_article',
                kwargs={'article_id': article.pk},
            )
        )

    if request.POST and "new-round" in request.POST:
        logic.new_typesetting_round(article, rounds, request.user)
        messages.add_message(
            request,
            messages.INFO,
            'New typesetting round created.',
        )

        return redirect(
            reverse(
                'typesetting_article',
                kwargs={'article_id': article.pk},
            )
        )

    elif request.POST and "complete-typesetting" in request.POST:
        logic.complete_typesetting(request, article)


    template = 'typesetting/typesetting_article.html'
    context = {
        'article': article,
        'rounds': rounds,
        'galleys': galleys,
        'manuscript_files': manuscript_files,
        'pending_tasks': logic.typesetting_pending_tasks(rounds[0]),
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_claim_article(request, article_id, action):
    """
    Allows a PM or Editor to claim or unclaim an article.
    :param request: HttpRequest
    :param article_id: int, Article object PK
    :param action: string, either claim or release
    :return: HttpRedirect
    """
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )

    if not hasattr(article, 'typesettingclaim'):

        models.TypesettingClaim.objects.get_or_create(
            editor=request.user,
            article=article,
        )

        messages.add_message(
            request,
            messages.SUCCESS,
            'Article claim successful.'
        )

    elif action == 'unclaim' and article.typesettingclaim.editor == request.user:
        article.typesettingclaim.delete()
        messages.add_message(
            request,
            messages.SUCCESS,
            'Article successfully released.',
        )

    else:
        messages.add_message(
            request,
            messages.ERROR,
            'This article is already being managed by {}.'.format(
                article.typesettingclaim.editor.full_name(),
            )
        )

    return redirect(
        reverse(
            'typesetting_article',
            kwargs={'article_id': article.pk},
        )
    )


@require_POST
@decorators.has_journal
@decorators.typesetting_user_or_production_user_or_editor_required
def typesetting_upload_galley(request, article_id, assignment_id=None):
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )

    try:
        if 'xml' in request.POST:
            for uploaded_file in request.FILES.getlist('xml-file'):
                production_logic.save_galley(
                    article,
                    request,
                    uploaded_file,
                    True,
                )
    except TypeError as exc:
        messages.add_message(request, messages.ERROR, str(exc))
    except UnicodeDecodeError:
        messages.add_message(request, messages.ERROR,
                             "Uploaded file is not UTF-8 encoded")

    if 'pdf' in request.POST:
        for uploaded_file in request.FILES.getlist('pdf-file'):
            production_logic.save_galley(
                article,
                request,
                uploaded_file,
                True,
                "PDF",
            )

    if 'other' in request.POST:
        for uploaded_file in request.FILES.getlist('other-file'):
            production_logic.save_galley(
                article,
                request,
                uploaded_file,
                True,
                "Other",
            )

    if 'prod' in request.POST:
        for uploaded_file in request.FILES.getlist('prod-file'):
            production_logic.save_prod_file(
                article,
                request,
                uploaded_file,
                'Production Ready File',
            )

    if assignment_id:

        assignment = get_object_or_404(
            models.TypesettingAssignment,
            pk=assignment_id,
            typesetter=request.user,
        )

        return redirect(
            reverse(
                'typesetting_assignment',
                kwargs={'assignment_id': assignment.pk}
            )
        )

    return redirect(
        reverse(
            'typesetting_article',
            kwargs={'article_id': article.pk},
        )
    )


@decorators.has_journal
@decorators.typesetting_user_or_production_user_or_editor_required
def typesetting_edit_galley(request, galley_id, article_id):
    """
    Allows a typesetter or editor to edit a Galley file.
    :param request: HttpRequest object
    :param galley_id: Galley object PK
    :param article_id: Article PK, optional
    :return: HttpRedirect or HttpResponse
    """
    return_url = request.GET.get('return', None)

    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )
    galley = get_object_or_404(
        core_models.Galley,
        pk=galley_id,
        article=article,
    )
    if galley.label == 'XML':
        xsl_files = core_models.XSLFile.objects.all()
    else:
        xsl_files = None

    if request.POST:

        if 'delete' in request.POST:

            production_logic.handle_delete_request(
                request,
                galley,
                article=article,
                page="pm_edit",
            )
            if not return_url:
                return redirect(
                    reverse(
                        'typesetting_article',
                        kwargs={'article_id': article.pk},
                    )
                )
            else:
                return redirect(return_url)

        label = request.POST.get('label')

        if 'fixed-image-upload' in request.POST:
            if request.POST.get('datafile') is not None:
                production_logic.use_data_file_as_galley_image(
                    galley,
                    request,
                    label,
                )
            for uploaded_file in request.FILES.getlist('image'):
                production_logic.save_galley_image(
                    galley,
                    request,
                    uploaded_file,
                    label,
                    fixed=True,
                )

        if 'image-upload' in request.POST:
            for uploaded_file in request.FILES.getlist('image'):
                production_logic.save_galley_image(
                    galley,
                    request,
                    uploaded_file,
                    label,
                    fixed=False,
                )

        elif 'css-upload' in request.POST:
            for uploaded_file in request.FILES.getlist('css'):
                production_logic.save_galley_css(
                    galley,
                    request,
                    uploaded_file,
                    'galley-{0}.css'.format(galley.id),
                    label,
                )

        if 'galley-label' in request.POST:
            galley.label = request.POST.get('galley_label')
            galley.save()

        if 'replace-galley' in request.POST:
            production_logic.replace_galley_file(
                article, request,
                galley,
                request.FILES.get('galley'),
            )

        if 'xsl_file' in request.POST:
            xsl_file = get_object_or_404(core_models.XSLFile,
                                         pk=request.POST["xsl_file"])
            galley.xsl_file = xsl_file
            galley.save()

        return_path = '?return={return_url}'.format(
            return_url=return_url,
        ) if return_url else ''
        url = reverse(
            'typesetting_edit_galley',
            kwargs={'article_id': article.pk, 'galley_id': galley_id},
        )
        redirect_url = '{url}{return_path}'.format(
            url=url,
            return_path=return_path,
        )
        return redirect(redirect_url)

    template = 'typesetting/edit_galley.html'
    context = {
        'galley': galley,
        'article': galley.article,
        'image_names': production_logic.get_image_names(galley),
        'return_url': return_url,
        'data_files': article.data_figure_files.all(),
        'galley_images': galley.images.all(),
        'xsl_files': xsl_files,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_assign_typesetter(request, article_id):
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )

    typesetters = logic.get_typesetters(request.journal)
    files = logic.production_ready_files(article, file_objects=True)
    rounds = models.TypesettingRound.objects.filter(article=article)

    form = forms.AssignTypesetter(
        typesetters=typesetters,
        files=files,
        rounds=rounds,
    )

    if request.POST:
        form = forms.AssignTypesetter(
            request.POST,
            typesetters=typesetters,
            files=files,
            rounds=rounds,
        )

        if form.is_valid():
            assignment = form.save()

            assignment.manager = request.user
            assignment.save()

            messages.add_message(
                request,
                messages.SUCCESS,
                'Assignment created.'
            )

            return redirect(
                reverse(
                    'typesetting_notify_typesetter',
                    kwargs={
                        'article_id': article.pk,
                        'assignment_id': assignment.pk
                    }
                )
            )

    template = 'typesetting/assign_typesetter.html'
    context = {
        'article': article,
        'typesetters': typesetters,
        'files': logic.production_ready_files(article),
        'form': form,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_notify_typesetter(request, article_id, assignment_id):
    """
    Allows the Editor to send a notification email to the typesetter.
    :param request: HttpRequest
    :param article_id: Article object PK
    :param assignment_id: TypesettingAssignment PK
    :return: HttpResponse
    """
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )
    assignment = get_object_or_404(
        models.TypesettingAssignment,
        pk=assignment_id,
    )

    if assignment.notified:
        messages.add_message(
            request,
            messages.WARNING,
            'A notification has already been sent for this assignment.'
        )

        return redirect(
            reverse(
                'typesetting_article',
                kwargs={'article_id': article.pk},
            )
        )

    if request.POST:
        message = request.POST.get('message')

        assignment.send_notification(
            message,
            request,
            skip=True if 'skip' in request.POST else False,
        )

        messages.add_message(
            request,
            messages.SUCCESS,
            'Assignment created.',
        )

        return redirect(
            reverse(
                'typesetting_article',
                kwargs={'article_id': article.pk}
            )
        )

    message = logic.get_typesetter_notification(
        assignment,
        article,
        request,
    )

    template = 'typesetting/notify_typesetter.html'
    context = {
        'article': article,
        'assignment': assignment,
        'message': message,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_review_assignment(request, article_id, assignment_id):
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )

    assignment = get_object_or_404(
        models.TypesettingAssignment,
        pk=assignment_id,
        round__article=article,
    )
    typesetters = logic.get_typesetters(request.journal)
    files = logic.production_ready_files(article, file_objects=True)
    rounds = models.TypesettingRound.objects.filter(article=article)
    edit_form = forms.AssignTypesetter(
        typesetters=typesetters,
        files=files,
        rounds=rounds,
        instance=assignment
    )

    galleys = core_models.Galley.objects.filter(
        article=assignment.round.article,
    )

    decision_form = forms.ManagerDecision()

    if request.POST and "edit" in request.POST:
        edit_form = forms.AssignTypesetter(
            request.POST,
            typesetters=typesetters,
            files=files,
            rounds=rounds,
            instance=assignment,
        )

        if edit_form.is_valid():
            assignment = edit_form.save()

            assignment.manager = request.user
            assignment.save()

            messages.add_message(
                request,
                messages.SUCCESS,
                'Assignment updated.'
            )
    elif request.POST and "delete" in request.POST:
        assignment.delete(request.user)
        return redirect(
            reverse(
                'typesetting_article',
                kwargs={'article_id': article.pk},
            )
        )
    elif request.POST and "reopen" in request.POST:
        assignment.reopen(request.user)
        return redirect(
            reverse(
                'typesetting_notify_typesetter',
                kwargs={
                    'article_id': article.pk,
                    'assignment_id': assignment.pk
                }
            )
        )

    elif request.POST and "decision" in request.POST:
        decision_form = forms.ManagerDecision(
            request.POST,
            instance=assignment,
        )

        if decision_form.is_valid():
            decision_form.save()
            return redirect(
                reverse(
                    'typesetting_article',
                    kwargs={'article_id': article.pk},
                )
            )

    template = 'typesetting/typesetting_review_assignment.html'
    context = {
        'article': article,
        'assignment': assignment,
        'galleys': galleys,
        'form': edit_form,
        'typesetters': typesetters,
        'files': logic.production_ready_files(article),
        'decision_form': decision_form,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.typesetter_user_required
def typesetting_assignments(request):
    assignments = models.TypesettingAssignment.objects.filter(
        typesetter=request.user,
        round__article__journal=request.journal,
        completed__isnull=True,
    )

    template = 'typesetting/typesetting_assignments.html'
    context = {
        'assignments': assignments,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.typesetter_user_required
def typesetting_typesetter_download_file(request, assignment_id, file_id):
    assignment = get_object_or_404(
        models.TypesettingAssignment,
        pk=assignment_id,
        typesetter=request.user,
        completed__isnull=True,
        round__article__journal=request.journal,
    )

    file = get_object_or_404(
        core_models.File,
        pk=file_id,
        article_id=assignment.round.article.pk,
    )

    if file in assignment.files_to_typeset.all():
        return files.serve_any_file(
            request,
            file,
            path_parts=('articles', assignment.round.article.pk),
        )
    else:
        raise PermissionDenied(
            'You do not have permission to view this file.',
        )


@decorators.has_journal
@decorators.typesetter_user_required
def typesetting_assignment(request, assignment_id):
    assignment = get_object_or_404(
        models.TypesettingAssignment,
        pk=assignment_id,
        typesetter=request.user,
        completed__isnull=True,
        round__article__journal=request.journal
    )
    galleys = core_models.Galley.objects.filter(
        article=assignment.round.article,
    )

    form = forms.TypesetterDecision()

    if request.POST:

        if assignment.cancelled:
            messages.add_message(
                request,
                messages.WARNING,
                'The manager for this article has cancelled this typesetting'
                'task. No further changes are allowed',
            )
            return redirect(reverse(
                'typesetting_assignment',
                kwargs={'assignment_id': assignment.pk},
            ))


        if 'complete_typesetting' in request.POST:
            note = request.POST.get('note_from_typesetter', None)
            assignment.complete(note, galleys)
            assignment.send_complete_notification(request)

            return redirect(reverse('typesetting_assignments'))

        form = forms.TypesetterDecision(request.POST)

        if form.is_valid():
            note = form.cleaned_data.get('note', 'No note supplied.')
            decision = form.cleaned_data.get('decision')
            if decision == 'accept':
                assignment.accepted = timezone.now()
                assignment.save()
                assignment.send_decision_notification(request, note, decision)
                return redirect(reverse(
                    'typesetting_assignment',
                    kwargs={'assignment_id': assignment.pk},
                ))
            else:
                assignment.completed = timezone.now()
                assignment.save()
                assignment.send_decision_notification(request, note, decision)
                messages.add_message(
                    request,
                    messages.INFO,
                    'Thanks, the manager has been informed.'
                )

                return redirect(reverse('typesetting_assignments'))

    template = 'typesetting/typesetting_assignment.html'
    context = {
        'assignment': assignment,
        'article': assignment.round.article,
        'form': form,
        'galleys': galleys,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_assign_proofreader(request, article_id):
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )
    rounds = models.TypesettingRound.objects.filter(article=article)
    proofreaders = logic.get_proofreaders(article, rounds[0])
    galleys = core_models.Galley.objects.filter(
        article=article,
    )

    if not galleys:
        messages.add_message(
            request,
            messages.WARNING,
            'You cannot assign a proofreader without Galley Files.',
        )

        return redirect(reverse(
            'typesetting_article',
            kwargs={'article_id': article.pk},
        ))

    form = forms.AssignProofreader(
        proofreaders=proofreaders,
        round=rounds[0],
        user=request.user,
    )

    if request.POST:
        form = forms.AssignProofreader(
            request.POST,
            proofreaders=proofreaders,
            round=rounds[0],
            user=request.user,
        )

        if form.is_valid():
            assignment = form.save()

            utils_models.LogEntry.add_entry(
                types='Proofreader Assigned',
                description='{} assigned as a proofreader by {}'.format(
                    assignment.proofreader.full_name(),
                    request.user,
                ),
                level='Info',
                actor=request.user,
                target=article,
            )

            messages.add_message(
                request,
                messages.SUCCESS,
                'Proofing Assignment created.'
            )

            return redirect(
                reverse(
                    'typesetting_notify_proofreader',
                    kwargs={
                        'article_id': article.pk,
                        'assignment_id': assignment.pk,
                    }
                )
            )

    template = 'typesetting/typesetting_assign_proofreader.html'
    context = {
        'article': article,
        'proofreaders': proofreaders,
        'form': form,
        'galleys': galleys,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_notify_proofreader(request, article_id, assignment_id):
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )
    assignment = get_object_or_404(
        models.GalleyProofing,
        pk=assignment_id,
        completed__isnull=True,
        notified=False,
    )
    message = logic.get_proofreader_notification(
        assignment,
        article,
        request,
    )

    if request.POST:
        message = request.POST.get('message')
        assignment.send_assignment_notification(
            request,
            message,
            skip=True if 'skip' in request.POST else False
        )

        messages.add_message(
            request,
            messages.SUCCESS,
            'Assignment created',
        )

        return redirect(
            reverse(
                'typesetting_article',
                kwargs={'article_id': article.pk},
            )
        )

    template = 'typesetting/typesetting_notify_proofreader.html'
    context = {
        'article': article,
        'assignment': assignment,
        'message': message,
    }

    return render(request, template, context)


@decorators.has_journal
@decorators.production_user_or_editor_required
def typesetting_manage_proofing_assignment(request, article_id, assignment_id):
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )
    assignment = get_object_or_404(
        models.GalleyProofing,
        pk=assignment_id,
    )
    rounds = models.TypesettingRound.objects.filter(article=article)
    proofreaders = logic.get_proofreaders(
        article,
        rounds[0],
        assignment=assignment,
    )

    form = forms.EditProofingAssignment(
        instance=assignment,
    )

    if request.POST:

        if 'action' in request.POST:

            action = request.POST.get('action')

            if action == 'cancel':
                assignment.cancel()

                utils_models.LogEntry.add_entry(
                    types='Proofreading Assignment Cancelled',
                    description='Proofing by {} cancelled by {}'.format(
                        assignment.proofreader.full_name(),
                        request.user,
                    ),
                    level='Info',
                    actor=request.user,
                    target=article,
                )

                messages.add_message(
                    request,
                    messages.SUCCESS,
                    'Proofing task cancelled.',
                )
            elif action == 'reset':
                assignment.reset()

                utils_models.LogEntry.add_entry(
                    types='Proofreading Assignment Reset',
                    description='Proofing by {} reset by {}'.format(
                        assignment.proofreader.full_name(),
                        request.user,
                    ),
                    level='Info',
                    actor=request.user,
                    target=article,
                )

                messages.add_message(
                    request,
                    messages.SUCCESS,
                    'Proofing task reset.',
                )

            return redirect(
                reverse(
                    'typesetting_article',
                    kwargs={'article_id': article.pk},
                )
            )

        form = forms.EditProofingAssignment(
            request.POST,
            instance=assignment,
        )

        if form.is_valid():
            form.save()
            messages.add_message(
                request,
                messages.SUCCESS,
                'Assignment updated.',
            )

            return redirect(
                reverse(
                    'typesetting_manage_proofing_assignment',
                    kwargs={
                        'article_id': article.pk,
                        'assignment_id': assignment.pk,
                    }
                )
            )

    template = 'typesetting/typesetting_manage_proofing_assignment.html'
    context = {
        'article': article,
        'assignment': assignment,
        'proofreaders': proofreaders,
        'form': form,
    }

    return render(request, template, context)


@decorators.proofreader_user_required
def typesetting_proofreading_assignments(request):
    assignments = models.GalleyProofing.objects.filter(
        proofreader=request.user,
    )

    template = 'typesetting/typesetting_proofing_assignments.html'
    context = {
        'assignments': assignments,
    }

    return render(request, template, context)


@decorators.proofreader_for_article_required
def typesetting_proofreading_assignment(request, assignment_id):
    assignment = get_object_or_404(
        models.GalleyProofing,
        pk=assignment_id,
        completed__isnull=True,
        cancelled=False,
    )
    galleys = core_models.Galley.objects.filter(
        article=assignment.round.article,
    )

    form = forms.ProofingForm(instance=assignment)

    if request.POST:
        form = forms.ProofingForm(request.POST, instance=assignment)

        if form.is_valid():
            form.save()

        if 'proofing_file' in request.POST:
            logic.handle_proofreader_file(
                request,
                assignment,
                assignment.round.article,
            )

        if 'complete' in request.POST:
            unproofed_galleys = assignment.unproofed_galleys(galleys)
            if not unproofed_galleys:
                assignment.complete()
                messages.add_message(
                    request,
                    messages.SUCCESS,
                    'Proofreading Assignment complete.',
                )
                return redirect(
                    reverse(
                        'core_dashboard',
                    )
                )
            else:
                messages.add_message(
                    request,
                    messages.WARNING,
                    'You must proof {}.'.format(
                        ", ".join([g.label for g in unproofed_galleys])
                    )
                )

        return redirect(
            reverse(
                'typesetting_proofreading_assignment',
                kwargs={'assignment_id': assignment.pk},
            )
        )

    template = 'typesetting/typesetting_proofreading_assignment.html'
    context = {
        'assignment': assignment,
        'galleys': galleys,
        'form': form,
    }

    return render(request, template, context)


@security.proofreader_for_article_required
def typesetting_preview_galley(
        request,
        article_id,
        galley_id,
        assignment_id=None,
):
    """
    Displays a preview of a galley object
    :param request: HttpRequest object
    :param assignment_id: ProofingTask object PK
    :param galley_id: Galley object PK
    :param article_id: Article object PK
    :return: HttpResponse
    """
    proofing_task = None
    article = get_object_or_404(
        submission_models.Article,
        pk=article_id,
        journal=request.journal,
    )
    if assignment_id:
        proofing_task = get_object_or_404(
            models.GalleyProofing,
            pk=assignment_id,
            round__article=article,
        )
        galley = get_object_or_404(
            core_models.Galley,
            pk=galley_id,
            article_id=article.pk,
        )
        proofing_task.proofed_files.add(galley)
    elif request.user.has_an_editor_role(request):
        galley = get_object_or_404(
            core_models.Galley,
            pk=galley_id,
            article_id=article.pk,
        )
    else:
        raise PermissionDenied

    if galley.type == 'xml' or galley.type == 'html':
        template = 'journal/article.html'
    elif galley.type == 'epub':
        template = 'proofing/preview/epub.html'
    else:
        template = 'typesetting/preview_embedded.html'

    context = {
        'proofing_task': proofing_task,
        'galley': galley,
        'article': article if article else proofing_task.round.article,
        'identifier_type': 'id',
        'identifier': article.pk if article else proofing_task.round.article.pk,
        'article_content': galley.file_content(),
    }

    return render(request, template, context)


@security.proofreader_for_article_required
def typesetting_proofing_download(request, article_id, assignment_id, file_id):
    """
    Serves a galley for proofreader
    """
    assignment = get_object_or_404(
        models.GalleyProofing,
        pk=assignment_id,
        round__article__id=article_id,
    )
    file = get_object_or_404(core_models.File, pk=file_id)
    try:
        galley = core_models.Galley.objects.get(
            article_id=assignment.round.article.pk,
            file=file,
        )
        assignment.proofed_files.add(galley)
        return files.serve_file(request, file, assignment.round.article)
    except core_models.Galley.DoesNotExist:
        messages.add_message(
            request,
            messages.WARNING,
            'Requested file is not a galley for proofing',
        )
        return redirect(request.META.get('HTTP_REFERER'))


@security.proofreader_for_article_required
def preview_figure(
        request,
        galley_id,
        file_name,
        assignment_id=None,
        article_id=None
):
    if assignment_id:
        assignment = get_object_or_404(
            models.GalleyProofing,
            pk=assignment_id,
        )
        galley = get_object_or_404(
            core_models.Galley,
            pk=galley_id,
            article_id=assignment.round.article.pk,
        )
    elif article_id and request.user.has_an_editor_role(request):
        article = get_object_or_404(
            submission_models.Article,
            pk=article_id,
            journal=request.journal,
        )
        galley = get_object_or_404(
            core_models.Galley,
            pk=galley_id,
            article_id=article.pk,
        )
    else:
        raise PermissionDenied

    return article_figure(request, galley.article.pk, galley.pk, file_name)
