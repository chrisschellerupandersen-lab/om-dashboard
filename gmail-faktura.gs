/**
 * ORGANIC MARKET GREVE — Gmail: Organic Bakery-fakturaer (PDF) → Dashboard
 *
 * Overvåger Gmail for Organic Bakery-fakturaer fra e-conomic
 * (afsender post@e-conomic.com, reply-to rmk@betterbread.dk, emne "Fakturanr. …",
 *  PDF-vedhæftning "Faktura_N.pdf"), sender PDF'en (base64) til dashboardet,
 *  som parser den og gemmer faktura + varelinjer (fragt + reel ugekost +
 *  afstemning mod portal-bestillingen).
 *
 * BEMÆRK: post@e-conomic.com sender også ANDRE leverandørers fakturaer. Derfor
 * validerer dashboardet at PDF'en er en Organic Bakery-faktura (markør + PROD-koder)
 * og springer alt andet over. Scriptet arkiverer kun ved 'ok' eller bevidst 'skip'.
 *
 * OPSÆTNING (én gang):
 *   1. Indsæt filen i samme Apps Script-projekt som de øvrige scripts.
 *   2. Kør 'opretFakturaTrigger' én gang → godkend adgang (auto-import hver time).
 *   3. (Valgfrit) Kør 'testFakturaSenaste' for at teste mod nyeste faktura (dry_run).
 */

// ── KONFIGURATION ─────────────────────────────────────────────────────────────
const FAK_DASHBOARD_URL = 'https://om-dashboard-production-0f3a.up.railway.app/api/bageri/faktura-mail';
const FAK_SECRET        = 'OM-Greve-2026-Hemlig';
const FAK_AFSENDER      = 'post@e-conomic.com';
// Snæver søgning: e-conomic-faktura MED PDF. Bageri-værnet ligger i dashboardet.
const FAK_SØGEORD       = 'from:' + FAK_AFSENDER + ' subject:(Fakturanr) has:attachment filename:pdf';
const FAK_LABEL         = 'bageri-faktura-sendt';

// ── HOVEDFUNKTION (kør via trigger) ───────────────────────────────────────────
function tjekBageriFakturaer() {
  var label = GmailApp.getUserLabelByName(FAK_LABEL) || GmailApp.createLabel(FAK_LABEL);
  var tråde = GmailApp.search(FAK_SØGEORD + ' -label:' + FAK_LABEL, 0, 10);
  if (tråde.length === 0) { Logger.log('Ingen nye bageri-fakturaer'); return; }
  Logger.log('Fandt ' + tråde.length + ' mulig(e) faktura-mail(s)');

  for (var t = 0; t < tråde.length; t++) {
    var besked = tråde[t].getMessages().slice(-1)[0];
    try {
      var res = sendFaktura(besked, false);
      if (res.ok) {
        tråde[t].addLabel(label);
        tråde[t].moveToArchive();
        Logger.log('✓ Faktura #' + res.fakturanr + ' → uge ' + res.uge + '/' + res.aar +
                   ' (' + res.antal_varer + ' varer, fragt ' + res.fragt_kr + ' kr, total ' + res.total_kr + ' kr)');
      } else if (res.sprunget_over) {
        // Ikke en bageri-faktura (anden leverandør) — markér så vi ikke prøver igen.
        tråde[t].addLabel(label);
        Logger.log('⤼ Sprunget over: ' + (res.grund || 'ikke bageri-faktura'));
      } else {
        Logger.log('⚠ Uventet svar: ' + JSON.stringify(res));
      }
    } catch (e) {
      Logger.log('✗ Fejl på "' + besked.getSubject() + '": ' + e.toString());
    }
  }
}

// ── SEND ÉN FAKTURA (PDF base64) TIL DASHBOARD ────────────────────────────────
function sendFaktura(besked, dryRun) {
  var pdf = null;
  var atts = besked.getAttachments();
  for (var i = 0; i < atts.length; i++) {
    if (/\.pdf$/i.test(atts[i].getName())) { pdf = atts[i]; break; }
  }
  if (!pdf) throw new Error('Ingen PDF-vedhæftning');

  var payload = {
    secret:     FAK_SECRET,
    afsender:   besked.getFrom(),
    dato:       besked.getDate().toISOString(),
    pdf_base64: Utilities.base64Encode(pdf.getBytes()),
    dry_run:    !!dryRun
  };
  var svar = UrlFetchApp.fetch(FAK_DASHBOARD_URL, {
    method: 'POST', contentType: 'application/json',
    payload: JSON.stringify(payload), muteHttpExceptions: true,
    headers: { 'X-Webhook-Secret': FAK_SECRET }
  });
  var kode = svar.getResponseCode();
  var txt  = svar.getContentText();
  Logger.log('Dashboard svar: ' + kode + ' - ' + txt);
  if (kode !== 200) throw new Error('Dashboard svarede ' + kode + ': ' + txt);
  return JSON.parse(txt);
}

// ── TEST MOD NYESTE FAKTURA (gemmer IKKE — dry_run) ───────────────────────────
function testFakturaSenaste() {
  var tråde = GmailApp.search(FAK_SØGEORD, 0, 1);
  if (tråde.length === 0) { Logger.log('Ingen faktura-mails fundet'); return; }
  var besked = tråde[0].getMessages().slice(-1)[0];
  Logger.log('Emne: ' + besked.getSubject());
  var res = sendFaktura(besked, true);
  Logger.log('TEST (dry_run): ' + JSON.stringify(res));
}

// ── OPSÆT TRIGGER (kør én gang manuelt) ───────────────────────────────────────
function opretFakturaTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'tjekBageriFakturaer') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
  ScriptApp.newTrigger('tjekBageriFakturaer').timeBased().everyHours(1).create();
  Logger.log('✓ Trigger oprettet: tjekBageriFakturaer kører hver time');
}
