from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["demo"])


@router.get("/demo/application", response_class=HTMLResponse, include_in_schema=False)
def demo_application():
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Demo AI Working Student — Acme Labs</title>
<style>body{font-family:system-ui;max-width:760px;margin:40px auto;padding:0 20px;color:#152238}
label{display:block;font-weight:650;margin-top:16px}input,select,textarea{width:100%;padding:10px;margin-top:6px;box-sizing:border-box}
button{margin-top:24px;padding:12px 18px;background:#193c36;color:white;border:0;border-radius:7px}</style></head>
<body><main><p class="company">Acme Labs</p><h1>Working Student AI Engineering</h1>
<p class="location">Berlin, Germany · Hybrid</p>
<section id="job-description"><p>Join our AI team to build reliable Python and FastAPI services. Python is required. SQL is preferred. Experience with LLM applications is a plus.</p></section>
<form id="application-form">
<label for="first_name">First name</label><input id="first_name" name="first_name" required>
<label for="last_name">Last name</label><input id="last_name" name="last_name" required>
<label for="email">Email</label><input id="email" name="email" type="email" required>
<label for="phone">Phone</label><input id="phone" name="phone" type="tel" required>
<label for="university">University</label><input id="university" name="university" required>
<label for="linkedin">LinkedIn Profile</label><input id="linkedin" name="linkedin" type="url" required>
<label for="resume">Resume upload</label><input id="resume" name="resume" type="file" accept="application/pdf" required>
<label for="authorization">Are you legally authorized to work in Germany?</label>
<select id="authorization" name="authorization" required><option value="">Choose…</option><option>Yes</option><option>No</option></select>
<label for="start_date">Available start date</label><input id="start_date" name="start_date" type="date" required>
<label for="motivation">Why are you interested in this role?</label><textarea id="motivation" name="motivation" maxlength="600" required></textarea>
<button type="submit">Submit application</button></form>
<div id="success" hidden><h2>Application received</h2><p>Demo submission completed.</p></div>
</main><script>document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();e.target.hidden=true;document.querySelector('#success').hidden=false;document.title='Application received — Acme Labs'})</script></body></html>"""
