# Publish this project

The source is committed locally. To create a repository from the extracted project with GitHub CLI:

```bash
gh auth login
git init -b main
git add .
git commit -m "Build PolyQuery heterogeneous query routing prototype"
gh repo create polyquery --public --source=. --remote=origin --push
```

Use `--private` instead of `--public` if you want to keep it private. If a repository with that name already exists, inspect it before selecting a new name or remote; do not overwrite it.

If you prefer the website, create an empty GitHub repository, copy its HTTPS URL, then run `git remote add origin YOUR_REPOSITORY_URL` and `git push -u origin main`. Authenticate through GitHub's normal credential flow; do not paste access tokens into chats or commit them.

The downloadable archive also contains `polyquery.bundle`, preserving the original local commit. To restore that exact history instead of initializing a new repository:

```bash
git clone polyquery.bundle polyquery-with-history
cd polyquery-with-history
git remote remove origin
gh repo create polyquery --public --source=. --remote=origin --push
```
