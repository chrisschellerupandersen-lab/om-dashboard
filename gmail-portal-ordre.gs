/**
 * ORGANIC MARKET GREVE — Gmail: Bageri portal-ordrer → Dashboard
 *
 * Overvåger Gmail for Organic Bakery portal-ordrebekræftelser
 * (Shopify-mail: "Ordren #NNNN er bekræftet" fra "Min butik",
 *  afsender store+106786619717@t.shopifyemail.com) og sender
 * mail-teksten til dashboardet, som parser varelinjerne og gemmer
 * dem som ugens bestilling (bruges i spild = bestilt − solgt − reddet).
 *
 * OPSÆTNING (én gang):
 *   1. Indsæt denne fil i samme Apps Script-projekt som salgsrapport-scriptet
 *      (eller et nyt projekt).
 *   2. Kør 'opretPortalTrigger' én gang → godkend adgang (auto-import hvert 30. min).
 *   3. Kør 'opretTorsdagTrigger' én gang → torsdags-tjek kl. 11 (alarm hvis ordre mangler).
 *   4. (Valgfrit) Kør 'testPortalSenesteOrdre' for at teste mod nyeste ordre.
 */

// ── KONFIGURATION ─────────────────────────────────────────────────────────────
const PORTAL_DASHBOARD_URL = 'https://om-dashboard-production-0f3a.up.railway.app/api/bageri/ordre-mail';
const PORTAL_SECRET        = 'OM-Greve-2026-Hemlig';
// Kun bageri-portalen — IKKE "Organic Market B2B" (store+81368744278@…).
const PORTAL_AFSENDER      = 'store+106786619717@t.shopifyemail.com';
const PORTAL_SØGEORD       = 'from:' + PORTAL_AFSENDER + ' subject:(bekræftet)';
const PORTAL_LABEL         = 'portal-ordre-sendt';
// Torsdags-alarm: hvem får besked hvis ugens bestilling mangler. Tom = kontoens egen mail.
const ALERT_EMAIL          = '';
const BESTIL_DEADLINE       = 'torsdag kl. 11:00';

// ── HOVEDFUNKTION (kør via trigger) ───────────────────────────────────────────
function tjekPortalOrdrer() {
  var label = GmailApp.getUserLabelByName(PORTAL_LABEL) || GmailApp.createLabel(PORTAL_LABEL);
  var tråde = GmailApp.search(PORTAL_SØGEORD + ' -label:' + PORTAL_LABEL, 0, 10);

  if (tråde.length === 0) { Logger.log('Ingen nye portal-ordrer'); return; }
  Logger.log('Fandt ' + tråde.length + ' ny(e) portal-ordre(r)');

  for (var t = 0; t < tråde.length; t++) {
    var besked = tråde[t].getMessages().slice(-1)[0];
    try {
      var res = sendPortalOrdre(besked);
      if (res.ok) {
        tråde[t].addLabel(label);
        tråde[t].moveToArchive();
        Logger.log('✓ ' + besked.getSubject() + ' → uge ' + res.uge + '/' + res.aar +
                   ' (' + res.antal_varer + ' varer, ' + res.total_stk + ' stk)');
      } else {
        Logger.log('⚠ Sprunget over: ' + (res.grund || 'ukendt'));
      }
    } catch(e) {
      Logger.log('✗ Fejl på "' + besked.getSubject() + '": ' + e.toString());
    }
  }
}

// ── SEND ÉN ORDRE-MAIL TIL DASHBOARD ──────────────────────────────────────────
function sendPortalOrdre(besked) {
  var payload = {
    secret:   PORTAL_SECRET,
    afsender: besked.getFrom(),
    dato:     besked.getDate().toISOString(),
    body:     besked.getPlainBody()
  };
  var options = {
    method:             'POST',
    contentType:        'application/json',
    payload:            JSON.stringify(payload),
    muteHttpExceptions: true,
    headers:            { 'X-Webhook-Secret': PORTAL_SECRET }
  };
  var svar = UrlFetchApp.fetch(PORTAL_DASHBOARD_URL, options);
  var kode = svar.getResponseCode();
  var txt  = svar.getContentText();
  Logger.log('Dashboard svar: ' + kode + ' - ' + txt);
  if (kode !== 200) throw new Error('Dashboard svarede ' + kode + ': ' + txt);
  return JSON.parse(txt);
}

// ── OPSÆT TRIGGER (kør én gang manuelt) ───────────────────────────────────────
function opretPortalTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'tjekPortalOrdrer') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
  ScriptApp.newTrigger('tjekPortalOrdrer').timeBased().everyMinutes(30).create();
  Logger.log('✓ Trigger oprettet: tjekPortalOrdrer kører hvert 30. minut');
}

// ── TEST MOD NYESTE ORDRE (gemmer IKKE — dry_run) ─────────────────────────────
function testPortalSenesteOrdre() {
  var tråde = GmailApp.search(PORTAL_SØGEORD, 0, 1);
  if (tråde.length === 0) { Logger.log('Ingen portal-ordrer fundet'); return; }
  var besked = tråde[0].getMessages().slice(-1)[0];
  var payload = {
    secret:   PORTAL_SECRET,
    afsender: besked.getFrom(),
    dato:     besked.getDate().toISOString(),
    body:     besked.getPlainBody(),
    dry_run:  true
  };
  var svar = UrlFetchApp.fetch(PORTAL_DASHBOARD_URL, {
    method: 'POST', contentType: 'application/json',
    payload: JSON.stringify(payload), muteHttpExceptions: true,
    headers: { 'X-Webhook-Secret': PORTAL_SECRET }
  });
  Logger.log('Emne: ' + besked.getSubject());
  Logger.log('TEST (dry_run) svar: ' + svar.getResponseCode() + ' - ' + svar.getContentText());
}

// ── TORSDAGS-TJEK kl. 11:00 (deadline) ────────────────────────────────────────
// Sikrer at ugens bestilling er lagt+importeret. Kører først auto-importen (så en
// netop-ankommen ordre kommer med), og sender DIG en alarm-mail hvis der IKKE er
// kommet en portal-ordre i denne uge (mandag→nu). Så opdager du en glemt bestilling
// mens der stadig er tid inden deadline.
function torsdagTjek() {
  // 1) Kør auto-import først — fanger en ordre der lige er landet
  try { tjekPortalOrdrer(); } catch (e) { Logger.log('Auto-import fejl: ' + e); }

  // 2) Er der kommet en portal-ordre i denne uge (siden mandag)?
  var traade = GmailApp.search(PORTAL_SØGEORD + ' newer_than:5d', 0, 5);
  var mandag = _mandagIDennesUge();
  var fundet = null;
  for (var t = 0; t < traade.length; t++) {
    var b = traade[t].getMessages().slice(-1)[0];
    if (b.getDate() >= mandag) { fundet = b; break; }
  }

  var modtager = ALERT_EMAIL || Session.getActiveUser().getEmail();
  if (fundet) {
    Logger.log('✓ Torsdags-tjek: ordre fundet denne uge (' + fundet.getSubject() + ') — alt ok');
    return;
  }
  // 3) Ingen ordre denne uge → alarm
  var emne = '⚠️ HUSK: Organic Bakery-bestilling mangler (deadline ' + BESTIL_DEADLINE + ')';
  var krop = 'Der er endnu IKKE registreret en bageri-bestilling i denne uge.\n\n'
           + 'Deadline er ' + BESTIL_DEADLINE + '. Log på portalen (organicbakery.dk) og '
           + 'læg ugens bestilling nu — så importeres den automatisk til dashboardet.\n\n'
           + '(Automatisk besked fra dashboardets torsdags-tjek.)';
  GmailApp.sendEmail(modtager, emne, krop);
  Logger.log('⚠️ Torsdags-tjek: INGEN ordre denne uge — alarm sendt til ' + modtager);
}

function _mandagIDennesUge() {
  var d = new Date();
  var wd = (d.getDay() + 6) % 7;           // man=0 … søn=6
  d.setDate(d.getDate() - wd);
  d.setHours(0, 0, 0, 0);
  return d;
}

// ── OPSÆT TORSDAGS-TRIGGER (kør én gang manuelt) ──────────────────────────────
function opretTorsdagTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'torsdagTjek') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
  ScriptApp.newTrigger('torsdagTjek')
    .timeBased().onWeekDay(ScriptApp.WeekDay.THURSDAY).atHour(11).create();
  Logger.log('✓ Trigger oprettet: torsdagTjek kører hver torsdag i 11-tiden');
}
