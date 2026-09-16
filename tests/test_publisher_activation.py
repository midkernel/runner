import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("publisher_activation", ROOT / "scripts/check_publisher_activation.py")
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


class PublisherActivationTests(unittest.TestCase):
    def check(self, role, repository=activation.REPOSITORY, ref="refs/heads/main", event="push"):
        return activation.publication_enabled(role, repository, ref, event)

    def test_unconfigured_publication_waits_for_bootstrap(self):
        self.assertFalse(self.check(""))

    def test_only_the_dedicated_role_activates_publication(self):
        self.assertTrue(self.check(activation.ROLE))
        self.assertTrue(self.check(activation.ROLE, event="workflow_dispatch"))

    def test_legacy_other_repository_and_malformed_roles_are_rejected(self):
        for role in ["arn:aws:iam::489470371031:role/midkernel-github-actions", activation.ROLE + "-other", "true", " "]:
            with self.subTest(role=role), self.assertRaises(ValueError):
                self.check(role)

    def test_non_main_and_non_release_contexts_are_rejected(self):
        for context in [{"repository": "midkernel/another-repo"}, {"ref": "refs/heads/feature"}, {"event": "pull_request"}]:
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.check(activation.ROLE, **context)

    def test_workflow_waits_for_activation_before_credentials(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("ECR_PUBLISHER_ROLE_ARN: ${{ vars.ECR_PUBLISHER_ROLE_ARN }}", workflow)
        ecr = workflow.split("  ecr:\n", 1)[1]
        self.assertIn("needs: [test, image, publisher-readiness]", ecr)
        self.assertIn("if: needs.publisher-readiness.outputs.enabled == 'true'", ecr)
        readiness = workflow.split("  publisher-readiness:\n", 1)[1].split("  image:\n", 1)[0]
        self.assertNotIn("id-token: write", readiness)
        self.assertNotIn("configure-aws-credentials", readiness)


if __name__ == "__main__":
    unittest.main()
