import mock
import pytest

from rest_framework.exceptions import PermissionDenied

from awx.api.versioning import reverse
from awx.api.serializers import JobTemplateSerializer
from awx.main.access import (
    BaseAccess,
    JobTemplateAccess,
    ScheduleAccess
)
from awx.main.models.jobs import JobTemplate
from awx.main.models.organization import Organization
from awx.main.models.schedules import Schedule


@pytest.fixture
def jt_linked(job_template_factory, credential, net_credential, vault_credential):
    '''
    A job template with a reasonably complete set of related objects to
    test RBAC and other functionality affected by related objects
    '''
    objects = job_template_factory(
        'testJT', organization='org1', project='proj1', inventory='inventory1',
        credential='cred1')
    jt = objects.job_template
    jt.credentials.add(vault_credential)
    jt.save()
    # Add AWS cloud credential and network credential
    jt.credentials.add(credential)
    jt.credentials.add(net_credential)
    return jt


@mock.patch.object(BaseAccess, 'check_license', return_value=None)
@pytest.mark.django_db
def test_job_template_access_superuser(check_license, user, deploy_jobtemplate):
    # GIVEN a superuser
    u = user('admin', True)
    # WHEN access to a job template is checked
    access = JobTemplateAccess(u)
    # THEN all access checks should pass
    assert access.can_read(deploy_jobtemplate)
    assert access.can_add({})


@pytest.mark.django_db
def test_job_template_access_read_level(jt_linked, rando):

    access = JobTemplateAccess(rando)
    jt_linked.project.read_role.members.add(rando)
    jt_linked.inventory.read_role.members.add(rando)
    jt_linked.get_deprecated_credential('ssh').read_role.members.add(rando)

    proj_pk = jt_linked.project.pk
    assert not access.can_add(dict(inventory=jt_linked.inventory.pk, project=proj_pk))
    assert not access.can_add(dict(credential=jt_linked.credential, project=proj_pk))
    assert not access.can_add(dict(vault_credential=jt_linked.vault_credential, project=proj_pk))

    for cred in jt_linked.credentials.all():
        assert not access.can_unattach(jt_linked, cred, 'credentials', {})


@pytest.mark.django_db
def test_job_template_access_use_level(jt_linked, rando):

    access = JobTemplateAccess(rando)
    jt_linked.project.use_role.members.add(rando)
    jt_linked.inventory.use_role.members.add(rando)
    jt_linked.get_deprecated_credential('ssh').use_role.members.add(rando)
    jt_linked.get_deprecated_credential('vault').use_role.members.add(rando)

    proj_pk = jt_linked.project.pk
    assert access.can_add(dict(inventory=jt_linked.inventory.pk, project=proj_pk))
    assert access.can_add(dict(credential=jt_linked.credential, project=proj_pk))
    assert access.can_add(dict(vault_credential=jt_linked.vault_credential, project=proj_pk))

    for cred in jt_linked.credentials.all():
        assert not access.can_unattach(jt_linked, cred, 'credentials', {})


@pytest.mark.django_db
@pytest.mark.parametrize("role_names", [("admin_role",), ("inventory_admin_role", "project_admin_role")])
def test_job_template_access_admin(role_names, jt_linked, rando):
    access = JobTemplateAccess(rando)
    # Appoint this user as admin of the organization
    #jt_linked.inventory.organization.admin_role.members.add(rando)
    for role_name in role_names:
        role = getattr(jt_linked.inventory.organization, role_name)
        role.members.add(rando)

    # Assign organization permission in the same way the create view does
    organization = jt_linked.inventory.organization
    jt_linked.get_deprecated_credential('ssh').admin_role.parents.add(organization.admin_role)

    proj_pk = jt_linked.project.pk
    assert access.can_add(dict(inventory=jt_linked.inventory.pk, project=proj_pk))
    assert access.can_add(dict(credential=jt_linked.credential, project=proj_pk))

    for cred in jt_linked.credentials.all():
        assert access.can_unattach(jt_linked, cred, 'credentials', {})

    assert access.can_read(jt_linked)
    assert access.can_delete(jt_linked)


@pytest.mark.django_db
def test_job_template_extra_credentials_prompts_access(
        rando, post, inventory, project, machine_credential, vault_credential):
    jt = JobTemplate.objects.create(
        name = 'test-jt',
        project = project,
        playbook = 'helloworld.yml',
        inventory = inventory,
        ask_credential_on_launch = True
    )
    jt.credentials.add(machine_credential)
    jt.execute_role.members.add(rando)
    r = post(
        reverse('api:job_template_launch', kwargs={'version': 'v2', 'pk': jt.id}),
        {'vault_credential': vault_credential.pk}, rando
    )
    assert r.status_code == 403


@pytest.mark.django_db
class TestJobTemplateCredentials:

    def test_job_template_cannot_add_extra_credentials(self, job_template, credential, rando):
        job_template.admin_role.members.add(rando)
        credential.read_role.members.add(rando)
        # without permission to credential, user can not attach it
        assert not JobTemplateAccess(rando).can_attach(
            job_template, credential, 'credentials', {})

    def test_job_template_can_add_extra_credentials(self, job_template, credential, rando):
        job_template.admin_role.members.add(rando)
        credential.use_role.members.add(rando)
        # user has permission to apply credential
        assert JobTemplateAccess(rando).can_attach(
            job_template, credential, 'credentials', {})

    def test_job_template_vault_cred_check(self, mocker, job_template, vault_credential, rando, project):
        # TODO: remove in 3.4
        job_template.admin_role.members.add(rando)
        # not allowed to use the vault cred
        # this is checked in the serializer validate method, not access.py
        view = mocker.MagicMock()
        view.request = mocker.MagicMock()
        view.request.user = rando
        serializer = JobTemplateSerializer(job_template, context={'view': view})
        with pytest.raises(PermissionDenied):
            serializer.validate({
                'vault_credential': vault_credential.pk,
                'project': project,  # necessary because job_template fixture fails validation
                'ask_inventory_on_launch': True,
            })

    def test_job_template_vault_cred_check_noop(self, mocker, job_template, vault_credential, rando, project):
        # TODO: remove in 3.4
        job_template.credentials.add(vault_credential)
        job_template.admin_role.members.add(rando)
        # not allowed to use the vault cred
        # this is checked in the serializer validate method, not access.py
        view = mocker.MagicMock()
        view.request = mocker.MagicMock()
        view.request.user = rando
        serializer = JobTemplateSerializer(job_template, context={'view': view})
        # should not raise error:
        serializer.validate({
            'vault_credential': vault_credential.pk,
            'project': project,  # necessary because job_template fixture fails validation
            'playbook': 'helloworld.yml',
            'ask_inventory_on_launch': True,
        })

    def test_new_jt_with_vault(self, mocker, vault_credential, project, rando):
        project.admin_role.members.add(rando)
        # TODO: remove in 3.4
        # this is checked in the serializer validate method, not access.py
        view = mocker.MagicMock()
        view.request = mocker.MagicMock()
        view.request.user = rando
        serializer = JobTemplateSerializer(context={'view': view})
        with pytest.raises(PermissionDenied):
            serializer.validate({
                'vault_credential': vault_credential.pk,
                'project': project,
                'playbook': 'helloworld.yml',
                'ask_inventory_on_launch': True,
                'name': 'asdf'
            })


@pytest.mark.django_db
class TestOrphanJobTemplate:

    def test_orphan_JT_readable_by_system_auditor(self, job_template, system_auditor):
        assert system_auditor.is_system_auditor
        assert job_template.project is None
        access = JobTemplateAccess(system_auditor)
        assert access.can_read(job_template)

    def test_system_admin_orphan_capabilities(self, job_template, admin_user):
        job_template.capabilities_cache = {'edit': False}
        access = JobTemplateAccess(admin_user)
        capabilities = access.get_user_capabilities(job_template, method_list=['edit'])
        assert capabilities['edit']


@pytest.mark.django_db
@pytest.mark.job_permissions
def test_job_template_creator_access(project, rando, post):

    project.admin_role.members.add(rando)
    with mock.patch(
            'awx.main.models.projects.ProjectOptions.playbooks',
            new_callable=mock.PropertyMock(return_value=['helloworld.yml'])):
        response = post(reverse('api:job_template_list'), dict(
            name='newly-created-jt',
            job_type='run',
            ask_inventory_on_launch=True,
            ask_credential_on_launch=True,
            project=project.pk,
            playbook='helloworld.yml'
        ), rando)

    assert response.status_code == 201
    jt_pk = response.data['id']
    jt_obj = JobTemplate.objects.get(pk=jt_pk)
    # Creating a JT should place the creator in the admin role
    assert rando in jt_obj.admin_role


@pytest.mark.django_db
def test_associate_label(label, user, job_template):
    access = JobTemplateAccess(user('joe', False))
    job_template.admin_role.members.add(user('joe', False))
    label.organization.read_role.members.add(user('joe', False))
    assert access.can_attach(job_template, label, 'labels', None)


@pytest.mark.django_db
class TestJobTemplateSchedules:

    rrule = 'DTSTART:20151117T050000Z RRULE:FREQ=DAILY;INTERVAL=1;COUNT=1'
    rrule2 = 'DTSTART:20151117T050000Z RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=1'

    @pytest.fixture
    def jt2(self):
        return JobTemplate.objects.create(name="other-jt")

    def test_move_schedule_to_JT_no_access(self, job_template, rando, jt2):
        schedule = Schedule.objects.create(unified_job_template=job_template, rrule=self.rrule)
        job_template.admin_role.members.add(rando)
        access = ScheduleAccess(rando)
        assert not access.can_change(schedule, data=dict(unified_job_template=jt2.pk))


    def test_move_schedule_from_JT_no_access(self, job_template, rando, jt2):
        schedule = Schedule.objects.create(unified_job_template=job_template, rrule=self.rrule)
        jt2.admin_role.members.add(rando)
        access = ScheduleAccess(rando)
        assert not access.can_change(schedule, data=dict(unified_job_template=jt2.pk))


    def test_can_create_schedule_with_execute(self, job_template, rando):
        job_template.execute_role.members.add(rando)
        access = ScheduleAccess(rando)
        assert access.can_add({'unified_job_template': job_template})


    def test_can_modify_ones_own_schedule(self, job_template, rando):
        job_template.execute_role.members.add(rando)
        schedule = Schedule.objects.create(unified_job_template=job_template, rrule=self.rrule, created_by=rando)
        access = ScheduleAccess(rando)
        assert access.can_change(schedule, {'rrule': self.rrule2})

    def test_prompts_access_checked(self, job_template, inventory, credential, rando):
        job_template.execute_role.members.add(rando)
        access = ScheduleAccess(rando)
        data = dict(
            unified_job_template=job_template,
            rrule=self.rrule,
            created_by=rando,
            inventory=inventory,
            credentials=[credential]
        )
        with mock.patch('awx.main.access.JobLaunchConfigAccess.can_add') as mock_add:
            mock_add.return_value = True
            assert access.can_add(data)
            mock_add.assert_called_once_with(data)
        data.pop('credentials')
        schedule = Schedule.objects.create(**data)
        with mock.patch('awx.main.access.JobLaunchConfigAccess.can_change') as mock_change:
            mock_change.return_value = True
            assert access.can_change(schedule, {'inventory': 42})
            mock_change.assert_called_once_with(schedule, {'inventory': 42})


@pytest.mark.django_db
def test_jt_org_ownership_change(user, jt_linked):
    admin1 = user('admin1')
    org1 = jt_linked.project.organization
    org1.admin_role.members.add(admin1)
    a1_access = JobTemplateAccess(admin1)

    assert a1_access.can_read(jt_linked)


    admin2 = user('admin2')
    org2 = Organization.objects.create(name='mrroboto', description='domo')
    org2.admin_role.members.add(admin2)
    a2_access = JobTemplateAccess(admin2)

    assert not a2_access.can_read(jt_linked)


    jt_linked.project.organization = org2
    jt_linked.project.save()
    jt_linked.inventory.organization = org2
    jt_linked.inventory.save()

    assert a2_access.can_read(jt_linked)
    assert not a1_access.can_read(jt_linked)
