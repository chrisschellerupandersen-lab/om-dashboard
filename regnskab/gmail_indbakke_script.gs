/**
 * Nimbo Regnskab — Gmail-indbakke-integration
 *
 * Finder ulæste/nye mails med PDF-vedhæftninger og sender dem videre til
 * /api/indbakke/upload, hvor de automatisk AI-læses og lander som et bilag
 * med status "afventer" klar til gennemsyn.
 *
 * OPSÆTNING:
 * 1. VIGTIGT: Gå til https://script.google.com MENS DU ER LOGGET IND PÅ SAMME GOOGLE-KONTO
 *    som I brugte til jeres eksisterende gmail-til-economic-script (forward_faktura.gs) —
 *    det er den postkasse hvor leverandørfakturaerne rent faktisk lander. Opret et nyt projekt.
 * 2. Slet standard-koden og indsæt hele denne fils indhold.
 * 3. Sæt token'et: Project Settings (tandhjulet i venstre menu) → Script Properties
 *    → "Add script property" → Property: INGEST_TOKEN, Value: (se nedenfor, hold hemmeligt).
 * 4. GMAIL_SOEGNING nedenfor genbruger samme afsender som det velfungerende
 *    gmail-til-economic-script (rmk@organicmarket.dk) — se kommentar ved variablen for
 *    hvordan flere afsendere tilføjes.
 * 5. Kør funktionen "opsaetTrigger" én gang manuelt (Kør-knappen, vælg den funktion)
 *    for at sætte automatisk kørsel hvert 15. minut op. Godkend adgang til Gmail når
 *    Google beder om det.
 * 6. Test med "hentOgSendFakturaer" manuelt først, og tjek Kørselslog (Executions)
 *    i venstre menu for fejl.
 */

// === Konfiguration ===
var INGEST_URL = 'https://regnskab-production.up.railway.app/api/indbakke/upload';

// Genbruger samme afsender som det velfungerende gmail-til-economic-script. Flere afsendere
// tilføjes med (from:a OR from:b) — IKKE from:(a OR b), som Apps Scripts GmailApp.search
// fejlfortolker (samme faldgrube som i forward_faktura.gs):
//   'in:inbox (from:rmk@organicmarket.dk OR from:anden@leverandoer.dk) has:attachment filename:pdf -label:faktura-sendt'
var GMAIL_SOEGNING = 'in:inbox from:rmk@organicmarket.dk has:attachment filename:pdf -label:faktura-sendt -label:faktura-fejl';

var LABEL_SENDT = 'faktura-sendt';
var LABEL_FEJL = 'faktura-fejl';
var MAKS_TRAADE_PR_KOERSEL = 50;


function hentOgSendFakturaer() {
  var token = PropertiesService.getScriptProperties().getProperty('INGEST_TOKEN');
  if (!token) {
    throw new Error('INGEST_TOKEN mangler — sæt den under Project Settings → Script Properties.');
  }

  var sendtLabel = hentEllerOpretLabel_(LABEL_SENDT);
  var fejlLabel = hentEllerOpretLabel_(LABEL_FEJL);

  var traade = GmailApp.search(GMAIL_SOEGNING, 0, MAKS_TRAADE_PR_KOERSEL);
  Logger.log('Fandt ' + traade.length + ' tråd(e) at behandle.');

  for (var i = 0; i < traade.length; i++) {
    var traad = traade[i];
    var beskeder = traad.getMessages();
    var traadOk = true;
    var antalSendt = 0;

    for (var j = 0; j < beskeder.length; j++) {
      var vedhaeftninger = beskeder[j].getAttachments({ includeInlineImages: false });
      for (var k = 0; k < vedhaeftninger.length; k++) {
        var fil = vedhaeftninger[k];
        if (fil.getContentType() !== 'application/pdf') continue;
        if (sendTilRegnskab_(fil, token)) {
          antalSendt++;
        } else {
          traadOk = false;
        }
      }
    }

    if (antalSendt > 0 && traadOk) {
      traad.addLabel(sendtLabel);
      traad.removeLabel(fejlLabel);
    } else if (!traadOk) {
      traad.addLabel(fejlLabel);
    }
  }
}


function sendTilRegnskab_(fil, token) {
  try {
    var response = UrlFetchApp.fetch(INGEST_URL, {
      method: 'post',
      headers: { 'X-Ingest-Token': token },
      payload: { fil: fil },
      muteHttpExceptions: true,
    });
    var kode = response.getResponseCode();
    if (kode === 200) {
      Logger.log('OK: ' + fil.getName());
      return true;
    }
    if (kode === 409) {
      // Allerede uploadet tidligere (samme fil genkendt på indhold) — ikke en fejl.
      Logger.log('Dublet (springes over): ' + fil.getName());
      return true;
    }
    Logger.log('FEJL (' + kode + ') ved ' + fil.getName() + ': ' + response.getContentText());
    return false;
  } catch (e) {
    Logger.log('Undtagelse ved ' + fil.getName() + ': ' + e);
    return false;
  }
}


function hentEllerOpretLabel_(navn) {
  var label = GmailApp.getUserLabelByName(navn);
  if (!label) label = GmailApp.createLabel(navn);
  return label;
}


/** Kør denne én gang manuelt for at sætte automatisk kørsel hvert 15. minut op. */
function opsaetTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'hentOgSendFakturaer') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('hentOgSendFakturaer').timeBased().everyMinutes(15).create();
  Logger.log('Trigger oprettet — kører nu automatisk hvert 15. minut.');
}
