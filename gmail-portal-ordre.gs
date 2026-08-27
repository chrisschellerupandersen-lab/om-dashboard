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
 *   2. Kør 'opretPortalTrigger' én gang → godkend adgang.
 *   3. (Valgfrit) Kør 'testPortalSenesteOrdre' for at teste mod nyeste ordre.
 */

// ── KONFIGURATION ─────────────────────────────────────────────────────────────
const PORTAL_DASHBOARD_URL = 'https://om-dashboard-production-0f3a.up.railway.app/api/bageri/ordre-mail';
const PORTAL_SECRET        = 'OM-Greve-2026-Hemlig';
// Kun bageri-portalen — IKKE "Organic Market B2B" (store+81368744278@…).
const PORTAL_AFSENDER      = 'store+106786619717@t.shopifyemail.com';
const PORTAL_SØGEORD       = 'from:' + PORTAL_AFSENDER + ' subject:(bekræftet)';
const PORTAL_LABEL         = 'portal-ordre-sendt';

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
