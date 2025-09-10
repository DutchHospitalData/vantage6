import unittest

from datetime import datetime, timedelta, timezone
import uuid
import yaml
import random
import string
from unittest.mock import patch
from flask_socketio import SocketIO
from http import HTTPStatus

from vantage6.server.model import Organization, Collaboration, Task, Run, Rule, User
from vantage6.server.model.rule import Scope, Operation
from vantage6.server.model.base import Database, DatabaseSessionManager
from vantage6.common.task_status import TaskStatus
from vantage6.common.globals import APPNAME, InstanceType
from vantage6.backend.common import test_context
from vantage6.server.globals import PACKAGE_FOLDER
from vantage6.server import ServerApp
from vantage6.server.controller.fixture import load
import sys
import types
sys.modules['uwsgi'] = types.ModuleType('uwsgi')


class TestTask(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """Called immediately before running a test method."""
        Database().connect("sqlite://", allow_drop_all=True)

        ctx = test_context.TestContext.from_external_config_file(
            PACKAGE_FOLDER, InstanceType.SERVER
        )

        # create server instance. Patch the start_background_task method
        # to prevent the server from starting a ping/pong thread that will
        # prevent the tests from starting
        with patch.object(SocketIO, "start_background_task"):
            server = ServerApp(ctx)
        cls.server = server

        file_ = str(
            PACKAGE_FOLDER / APPNAME / "server" / "_data" / "unittest_fixtures.yaml"
        )
        with open(file_) as f:
            cls.entities = yaml.safe_load(f.read())
        load(cls.entities)

        server.app.testing = True
        cls.app = server.app.test_client()

        cls.credentials = {
            "root": {"username": "root", "password": "root"},
            "admin": {"username": "frank-iknl", "password": "password"},
            "user": {"username": "melle-iknl", "password": "password"},
            "user-to-delete": {"username": "dont-use-me", "password": "password"},
        }

    @classmethod
    def tearDownClass(cls):
        Database().clear_data()

    @classmethod
    def setUp(cls):
        # set session.session
        cls.azure_config = {
            "container_name": "test-container",
            "blob_service_client": patch(
                "azure.storage.blob.BlobServiceClient"
            ).start(),
        }
        cls.uuid = str(uuid.uuid4())
        DatabaseSessionManager.get_session()

    @classmethod
    def tearDown(cls):
        # unset session.session
        DatabaseSessionManager.clear_session()
    
    def create_user(self, organization=None, rules=[], password="password"):
        if not organization:
            organization = Organization(name=str(uuid.uuid1()))
            organization.save()

        # user details
        username = random.choice(string.ascii_letters) + str(uuid.uuid1())

        # create a temporary organization
        user = User(
            username=username,
            password=password,
            organization=organization,
            email=f"{username}@test.org",
            rules=rules,
        )
        user.save()

        self.credentials[username] = {"username": username, "password": password}

        return user
    
    def login(self, type_="root"):
        with self.server.app.test_client() as client:
            tokens = client.post("/api/token/user", json=self.credentials[type_]).json
        if "access_token" in tokens:
            headers = {"Authorization": "Bearer {}".format(tokens["access_token"])}
            return headers
        else:
            print("something wrong, during login:")
            print(tokens)
            return None
    
    def create_user_and_login(self, organization=None, rules=[]):
        user = self.create_user(organization, rules)
        return self.login(user.username)
  
    def test_delete_task_and_blob(self):
        """Test the /api/task/<id>/status endpoint"""

        # Create organizations and collaboration
        org = Organization()
        org2 = Organization()
        col = Collaboration(organizations=[org, org2])
        col.save()

        # Create a task
        task = Task(collaboration=col, init_org=org)
        task.save()

        # Add runs to the task with valid statuses
        run1 = Run(task=task, status=TaskStatus.ACTIVE.value)
        run2 = Run(
            finished_at=datetime.now(timezone.utc) - timedelta(days=31),
            result=self.uuid,
            input="input",
            log="log should be preserved",
            status=TaskStatus.COMPLETED,
            task=task,
            blob_storage_used=True,
        )
        run1.save()
        run2.save()
        
        run1_id = run1.id
        run2_id = run2.id
        task_id = task.id
       # Test with organization permissions (should succeed for the same organization)
        rule = Rule.get_by_("task", Scope.GLOBAL, Operation.VIEW)
        headers = self.create_user_and_login(rules=[rule])
        result = self.app.get(f"/api/task/{task.id}/status", headers=headers)
        self.assertEqual(result.json["status"], TaskStatus.ACTIVE)
        
        # Delete task with runs
        rule = Rule.get_by_("task", Scope.GLOBAL, Operation.DELETE)
        headers = self.create_user_and_login(rules=[rule])
        results = self.app.delete(f"/api/task/{task.id}", headers=headers)
        self.assertEqual(results.status_code, HTTPStatus.OK)

        # Check if the runs and task are deleted
        self.assertIsNone(Run.get(run1_id))
        self.assertIsNone(Run.get(run2_id))
        self.assertIsNone(Task.get(task_id))

        # Cleanup
        org.delete()
        org2.delete()
        col.delete()

