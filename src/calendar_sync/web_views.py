"""Small server-rendered UI. No third-party scripts/assets or token rendering."""

from html import escape


CSS = '''
:root{color-scheme:light;--ink:#142b39;--muted:#526772;--line:#dce5e9;--brand:#136b64}
*{box-sizing:border-box}body{margin:0;background:#f4f7f8;color:var(--ink);font:16px/1.6 system-ui,sans-serif}
header{background:white;border-bottom:1px solid var(--line);padding:20px max(24px,calc((100vw - 1100px)/2));display:flex;justify-content:space-between;gap:20px;align-items:center}
.brand{font-weight:750;letter-spacing:-.5px;font-size:22px}.brand span{color:var(--brand)}main{max-width:1100px;margin:64px auto;padding:0 24px}
.eyebrow{color:var(--brand);text-transform:uppercase;letter-spacing:2px;font-weight:700;font-size:12px}h1{font-size:clamp(32px,5vw,48px);line-height:1.15;letter-spacing:-1.5px;margin:12px 0 20px}h2{font-size:20px;margin:0 0 12px}p{color:var(--muted);max-width:650px}a{color:var(--brand)}
.hero{padding:48px;background:white;border:1px solid var(--line);border-radius:20px;max-width:790px;box-shadow:0 16px 40px #19364208}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px;margin:32px 0}.card{padding:28px;background:white;border:1px solid var(--line);border-radius:16px}
.badge{display:inline-block;background:#e6f2ee;color:#215f50;border-radius:20px;padding:4px 12px;font-size:12px;font-weight:650}.quiet{color:var(--muted);font-size:14px}button,.button{display:inline-block;background:var(--brand);color:white;border:0;border-radius:9px;padding:12px 22px;text-decoration:none;font:inherit;font-weight:650;cursor:pointer}button.secondary{background:white;color:var(--ink);border:1px solid var(--line);padding:8px 16px}form{margin:0}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}footer{margin-top:36px;color:var(--muted);font-size:13px}.identity{display:flex;gap:16px;align-items:center;font-size:14px}@media(max-width:600px){main{margin:32px auto}.hero{padding:28px}header{align-items:flex-start}.identity{flex-direction:column;align-items:flex-end}table{font-size:13px}}
'''


def page(body,identity=''):
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Calendar Sync</title><link rel="stylesheet" href="/assets/app.css"></head><body><header><div class="brand">Calendar<span> Sync</span></div>'+identity+'</header><main>'+body+'<footer>Your calendar credentials stay on the server. Signing out does not stop an enabled sync worker.</footer></main></body></html>'


def login(csrf=None):
    if csrf:
        action = '<form method="post" action="/auth/login"><input type="hidden" name="csrf" value="'+escape(csrf,quote=True)+'"><button type="submit">Continue with Keycloak</button></form>'
    else:
        action = '<a class="button" href="/auth/login">Sign in</a>'
    return page('<section class="hero"><div class="eyebrow">Your calendars, connected</div><h1>A single place for<br>your calendar sync.</h1><p>Sign in with your organization account to view your connections and calendar pairs.</p>'+action+'<p class="quiet">Account connection and calendar setup will be available in a later update.</p></section>')


def error_page(status):
    title = 'Your account does not have access' if status==403 else 'Sign-in could not be completed'
    return page('<section class="hero"><div class="eyebrow">Calendar Sync</div><h1>'+title+'</h1><p>Please try signing in again. If the problem continues, contact your administrator.</p><a class="button" href="/auth/login">Return to sign in</a></section>')


def dashboard(display,csrf,connections,pairs):
    identity = '<div class="identity"><span>'+escape(display)+'</span><form method="post" action="/auth/logout"><input type="hidden" name="csrf" value="'+escape(csrf,quote=True)+'"><button class="secondary">Sign out</button></form></div>'
    body = '<div class="eyebrow">Your workspace</div><h1>Calendar overview</h1><p>Only connections and calendar pairs assigned to your account appear here.</p><div class="grid"><section class="card"><span class="badge">Read only</span><h2>Calendar connections</h2><p>Google and Nextcloud account connection is coming in a later update.</p></section><section class="card"><span class="badge">Not available yet</span><h2>Calendar pair setup</h2><p>Pair creation and two-way synchronization are not enabled in this version.</p></section></div>'
    body += '<section class="card"><h2>Your connections</h2>'
    if connections:
        body += '<table><thead><tr><th>Provider</th><th>Account</th><th>Status</th></tr></thead><tbody>'
        for item in connections:
            provider = {'google':'Google Calendar','nextcloud':'Nextcloud'}.get(item['provider'],'Calendar')
            account = 'Imported account' if item['account_id'].startswith('legacy:') else item['account_id']
            status = {'ready':'Connected','needs_verification':'Needs review','disconnected':'Disconnected'}.get(item['status'],'Unavailable')
            body += '<tr>'+''.join('<td>'+escape(str(value))+'</td>' for value in (provider,account,status))+'</tr>'
        body += '</tbody></table>'
    else:
        body += '<p>No connections have been assigned to your account.</p>'
    body += '</section><section class="card"><h2>Your calendar pairs</h2>'
    if pairs:
        body += '<ul>'+''.join('<li>'+escape(item['id'])+' — '+escape(item['state'])+' · Dry-run</li>' for item in pairs)+'</ul>'
    else:
        body += '<p>No calendar pairs have been assigned to your account.</p>'
    return page(body+'</section>',identity)
