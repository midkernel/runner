# Image publication activation

Merging this source change keeps validation active and stages image publication until infrastructure is ready. With no `ECR_PUBLISHER_ROLE_ARN` repository variable, the main workflow's **Publisher activation** job explicitly reports publication pending and requests no AWS credentials. Existing ECR images remain available. This is not a successful image release.

1. An authorized infrastructure operator follows `midkernel/infra`'s reviewed bootstrap in `docs/operations.md`. Provision and verify `arn:aws:iam::489470371031:role/midkernel-github-publish-runner` with exact main-only OIDC trust and the documented repository-scoped ECR permissions. Configure protected apply and private saved-plan storage as required there.
2. After bootstrap, set this repository's Actions variable **ECR_PUBLISHER_ROLE_ARN** to `arn:aws:iam::489470371031:role/midkernel-github-publish-runner`. This nonsecret variable explicitly activates publishing; any other nonempty value fails validation. The workflow never falls back to the legacy administrator role.
3. Dispatch **CI** from `main`. Confirm tests, image build, and **Push to ECR** all succeed, and record the published commit and digest. A readiness success alone does not prove role availability or publication. Complete this public runner release before publishing the private overlay that consumes its base image.
4. After both publisher migrations succeed, remove transitional legacy trust through a reviewed infrastructure change. Do not set app runtime image configuration from this repository.

The operator can unset the activation variable to pause future publication while retaining validation and existing images. This does not roll back images already published. Pull requests never receive AWS publisher credentials. The public Docker build remains credential-free and runs on pull requests and main.
