# SeekApply Assist Browser Extension

This is the production-style automation path for LinkedIn and ATS pages.

It runs inside your already logged-in Brave/Chrome tab, fills visible fields from
the local SeekApply knowledge base, highlights missing required fields, highlights
resume upload controls, and stops before final submit.

## Install In Brave

1. Open `brave://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this folder:
   `browser-extension/seekapply-assist`
5. Keep the SeekApply backend running at `http://127.0.0.1:8000`.

## Use

1. Open a LinkedIn Easy Apply modal or any company application page in Brave.
2. Click the SeekApply Assist extension icon.
3. Click `Autofill Current Page`.
4. Review highlighted fields.
5. Manually choose the resume file when a file input is highlighted.
6. Manually click final submit only after review.

The extension does not bypass login, CAPTCHA, or LinkedIn limits, and it does not
auto-submit applications.
