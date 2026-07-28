/**
 * Sender leverandørfakturaer (PDF) fra Gmail automatisk til Nimbo Regnskab.
 *
 * Hvad den gør:
 *   Finder mails i INDBAKKEN fra kendte leverandører med en PDF-vedhæftning,
 *   som endnu IKKE er sendt (mangler labelet "nimbosendt"), uploader hver PDF
 *   direkte til Nimbos indbakke-endpoint (AI læser og foreslår kontering —
 *   bilaget lander som "afventer" til din godkendelse, betaler/bogfører ALDRIG
 *   automatisk), og sætter labelet "nimbosendt" på mailen. Behandlede mails
 *   sendes derfor aldrig to gange.
 *
 * OPSÆTNING (engangs):
 *   1. https://script.google.com  →  Nyt projekt.
 *   2. Slet standardkoden, indsæt HELE denne fil, gem (diskette-ikon).
 *   3. Udfyld TARGET_URL og INGEST_TOKEN nedenfor (token står i Railway →
 *      regnskab-projektet → Variables → INGEST_TOKEN).
 *   4. Udfyld AFSENDERE med dine leverandørers mailadresser.
 *   5. Vælg funktionen "uploadInvoices" øverst og tryk ▶ Kør én gang.
 *      Godkend Gmail- og netværksadgangen (det er dit eget script).
 *   6. Tryk ⏰ (Triggers) i venstre menu → "Add Trigger":
 *        - Function: uploadInvoices · Event source: Time-driven · fx hver 30. min.
 *   Færdig — herefter kører den af sig selv.
 *
 * VIGTIGT ved FØRSTE kørsel:
 *   Den uploader ALLE fakturaer fra AFSENDERE der lige nu ligger åbne i
 *   indbakken uden "nimbosendt"-label. Er nogle allerede bogført i Nimbo, kan
 *   du på forhånd sætte labelet "nimbosendt" på dem, så de springes over.
 */

// ── Indstillinger ─────────────────────────────────────────────────────────────
var TARGET_URL   = 'https://regnskab-production.up.railway.app/api/indbakke/upload';
var INGEST_TOKEN = 'INDSÆT_DIN_TOKEN_HER';   // fra Railway → regnskab → Variables → INGEST_TOKEN

// Kendte leverandører. BEMÆRK: skriv (from:a OR from:b) — IKKE from:(a OR b),
// da Apps Scripts GmailApp.search misforstår from:(...)-formen.
var AFSENDERE  = '(from:leverandor1@example.dk OR from:leverandor2@example.dk)';
var LABEL_DONE = 'nimbosendt';   // sættes automatisk når faktura er uploadet
var MAX_TRAADE = 50;             // maks. tråde pr. kørsel (undgår timeout)
// ──────────────────────────────────────────────────────────────────────────────

function uploadInvoices() {
  var doneLabel = GmailApp.getUserLabelByName(LABEL_DONE) || GmailApp.createLabel(LABEL_DONE);

  var query = 'in:inbox ' + AFSENDERE + ' has:attachment -label:' + LABEL_DONE;
  var threads = GmailApp.search(query, 0, MAX_TRAADE);
  Logger.log('Søgning fandt ' + threads.length + ' tråd(e).');
  var uploadet = 0;

  for (var i = 0; i < threads.length; i++) {
    var traadUploadet = 0;
    var msgs = threads[i].getMessages();
    for (var j = 0; j < msgs.length; j++) {
      var atts = msgs[j].getAttachments();
      for (var k = 0; k < atts.length; k++) {
        if (/\.pdf$/i.test(atts[k].getName())) {
          if (uploadPdf_(atts[k])) {
            traadUploadet++;
            uploadet++;
          }
        }
      }
    }
    // Marker KUN som sendt hvis der faktisk blev uploadet en PDF fra tråden
    if (traadUploadet > 0) threads[i].addLabel(doneLabel);
  }

  Logger.log('Uploadede ' + uploadet + ' faktura(er) til Nimbo');
}

function uploadPdf_(blob) {
  var options = {
    method: 'post',
    headers: { 'X-Ingest-Token': INGEST_TOKEN },
    payload: { fil: blob },
    muteHttpExceptions: true,
  };
  var resp = UrlFetchApp.fetch(TARGET_URL, options);
  var kode = resp.getResponseCode();
  if (kode !== 200) {
    Logger.log('Fejl ved upload af "' + blob.getName() + '" (' + kode + '): ' + resp.getContentText());
    return false;
  }
  return true;
}
