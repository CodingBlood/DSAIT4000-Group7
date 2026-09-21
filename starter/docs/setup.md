# Setup & Git Workflow

## First-time setup
git clone https://github.com/CodingBlood/DSAIT4000-Group7.git
cd DSAIT4000-Group7

## Every time you start working on something
git checkout main
git pull origin main
git checkout -b <your-name>-develop     # only once; reuse the branch after

## Daily loop
1. Sync your branch with main:
   git checkout main
   git pull origin main
   git checkout <your-name>-develop
   git merge main

2. Make your changes.

3. Stage, commit, push:
   git add <files>
   git commit -m "short description of what changed"
   git push origin <your-name>-develop

4. Open a Pull Request on GitHub: <your-name>-develop -> main

5. After it's approved and merged: do NOT tick "delete branch" on GitHub
   (we're reusing branches, not creating a new one every time)

6. Sync again before your next session (see step 1)

## Rules
- Sync with main every day, or right after any merge
- Small, frequent commits > one giant commit
- Don't force-push to main, ever