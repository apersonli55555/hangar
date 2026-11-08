# Launching Hangar (Phase 1)

About an hour. Everything here is free. Steps marked **(you)** need your own
accounts or a click; I can do the rest once those exist.

## 0. Move the project to a permanent folder: DONE

The project lives in `C:\Users\apers\Hangar`.

## 1. Project email (you)

Create a new email just for Hangar (e.g. a new Gmail like `hangar.research.xyz@gmail.com`).
It goes in the User-Agent that the Internet Archive (and later SEC EDGAR) sees,
and in the Tiingo email. It keeps your personal address out of the code and logs.

## 2. Install Git: DONE

Git 2.56 is installed, and the folder is a Git repository on branch `main`
(nothing committed yet: the first commit waits for your GitHub no-reply email).

## 3. GitHub account and repository (you)

1. Sign up at github.com (the free plan is enough).
2. New repository: name `hangar`, **Public**, and *don't* add a README (we have one).
3. Copy the repository URL it shows, e.g. `https://github.com/<you>/hangar.git`.

## 4. First commit and push (me, after steps 0–3)

I'll run `git init`, check what's included (`.gitignore` leaves out the
built site and scratch files), commit, and push. The first push is about 45 MB,
mostly the archived pages in `data/raw/`.

## 5. Repository settings (you, in the browser)

1. **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `HANGAR_CONTACT`
   - Value: `Hangar student research project, <project email>`
   The daily job refuses to run without it.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Actions tab → daily-contracts → Run workflow.** The first run takes a few
   minutes: tests, fetch, build, site checks, deploy. When it's green, the site
   is at `https://<you>.github.io/hangar/`.

## 6. Analytics (you, optional but planned)

1. Sign up at goatcounter.com (free for non-commercial sites; no cookies, no
   personal data, so no cookie banner needed).
2. Choose a code, e.g. `hangar` (gives `hangar.goatcounter.com`).
3. **Settings → Secrets and variables → Actions → Variables → New variable**
   - Name: `GOATCOUNTER_CODE`, Value: `hangar`
4. Run the workflow again. Visits show up on your GoatCounter dashboard.

## 7. After launch

- Send the Tiingo email (`docs/tiingo_permission_request.md`), from the project email.
- Do the data audit (`REVIEWING.md`) and confirm the large amounts.
- Check the site on your phone.

## What runs every day after that

Mon–Sat, twice a day, GitHub Actions: runs all tests → fetches new announcements
→ rebuilds the data → builds the site → checks links/disclaimer/source links →
commits the data → publishes the site. If any step fails, nothing is published
and GitHub emails you.
