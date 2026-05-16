/**
 * ORGANIC MARKET GREVE — Gmail Automatisering
 *
 * Overvåger Gmail for Shopbox salgsrapport-mails og sender
 * filen automatisk til Railway som opdaterer samlet.html
 * og til OM Dashboard.
 */

// ── KONFIGURATION ─────────────────────────────────────────────────────────────
const RAILWAY_URL    = 'https://bestilling-app-production.up.railway.app/api/opdater-rapport';
const DASHBOARD_URL  = 'https://om-dashboard-production-0f3a.up.railway.app/api/opdater-rapport';
const WEBHOOK_SECRET = 'OM-Greve-2026-Hemlig';
const SØGEORD        = 'Varesalgsrapport';
const FRA_ADRESSE    = '';
const LABEL_NAVN     = 'rapport-sendt';

// ── HOVEDFUNKTION ─────────────────────────────────────────────────────────────
function tjekMail() {
  var label = GmailApp.getUserLabelByName(LABEL_NAVN) || GmailApp.createLabel(LABEL_NAVN);

  var søgestreng = SØGEORD + ' -label:' + LABEL_NAVN;
  if (FRA_ADRESSE) søgestreng += ' from:' + FRA_ADRESSE;

  var tråde = GmailApp.search(søgestreng, 0, 10);

  if (tråde.length === 0) {
    Logger.log('Ingen nye salgsrapporter fundet');
    return;
  }

  Logger.log('Fandt ' + tråde.length + ' ny(e) salgsrapport(er)');

  for (var t = 0; t < tråde.length; t++) {
    var beskeder     = tråde[t].getMessages();
    var sidsteBesked = beskeder[beskeder.length - 1];
    var mailKrop     = sidsteBesked.getPlainBody() + ' ' + sidsteBesked.getBody();
    var downloadUrl  = findShopboxLink(mailKrop);

    try {
      if (downloadUrl) {
        Logger.log('Henter fil fra Shopbox: ' + downloadUrl);
        var response = UrlFetchApp.fetch(downloadUrl, { muteHttpExceptions: true });

        if (response.getResponseCode() !== 200) {
          throw new Error('Shopbox svarede ' + response.getResponseCode());
        }

        var filBytes = response.getContent();
        Logger.log('Fil hentet (' + filBytes.length + ' bytes)');

        sendFilTilRailway(filBytes, 'salgsdata.xlsx', sidsteBesked.getDate().toISOString());
        sendDashboard(filBytes, 'salgsdata.xlsx', sidsteBesked.getDate().toISOString());

      } else {
        var vedhæftninger = sidsteBesked.getAttachments();
        var fandt = false;

        for (var v = 0; v < vedhæftninger.length; v++) {
          var fil = vedhæftninger[v];
          if (fil.getName().toLowerCase().indexOf('varesalgsrapport') >= 0 ||
              fil.getName().toLowerCase().indexOf('.xlsx') >= 0 ||
              fil.getName().toLowerCase().indexOf('.txt') >= 0) {
            Logger.log('Fandt vedhæftning: ' + fil.getName());
            sendFilTilRailway(fil.getBytes(), 'salgsdata.xlsx', sidsteBesked.getDate().toISOString());
            sendDashboard(fil.getBytes(), 'salgsdata.xlsx', sidsteBesked.getDate().toISOString());
            fandt = true;
            break;
          }
        }

        if (!fandt) {
          Logger.log('Hverken Shopbox-link eller vedhæftning fundet i mail: ' + sidsteBesked.getSubject());
          continue;
        }
      }

      tråde[t].addLabel(label);
      tråde[t].moveToArchive();
      Logger.log('✓ Rapport sendt, mail markeret og arkiveret under rapport-sendt');

    } catch(e) {
      Logger.log('✗ Fejl: ' + e.toString());
    }
  }
}

// ── FIND SHOPBOX DOWNLOAD-LINK ────────────────────────────────────────────────
function findShopboxLink(tekst) {
  var mønstre = [
    /https:\/\/api-prod\.shopbox\.com\/api\/v3\/saved-reports\/download-path[^\s"'<>]*/,
    /https:\/\/[^\s"'<>]*shopbox\.com[^\s"'<>]*download[^\s"'<>]*/,
    /https:\/\/[^\s"'<>]*shopbox\.com[^\s"'<>]*\.xlsx[^\s"'<>]*/,
  ];

  for (var i = 0; i < mønstre.length; i++) {
    var match = tekst.match(mønstre[i]);
    if (match) return match[0];
  }

  return null;
}

// ── SEND FIL TIL RAILWAY (bestillingsapp) ─────────────────────────────────────
function sendFilTilRailway(filBytes, filnavn, mailDato) {
  var base64 = Utilities.base64Encode(filBytes);

  var payload = {
    secret:  WEBHOOK_SECRET,
    filnavn: filnavn,
    data:    base64,
    dato:    mailDato
  };

  var options = {
    method:             'POST',
    contentType:        'application/json',
    payload:            JSON.stringify(payload),
    muteHttpExceptions: true,
    headers:            { 'X-Webhook-Secret': WEBHOOK_SECRET }
  };

  var svar       = UrlFetchApp.fetch(RAILWAY_URL, options);
  var statuskode = svar.getResponseCode();
  var svarTekst  = svar.getContentText();

  Logger.log('Railway svar: ' + statuskode + ' - ' + svarTekst);

  if (statuskode !== 200) throw new Error('Railway svarede ' + statuskode + ': ' + svarTekst);

  var json = JSON.parse(svarTekst);
  if (!json.ok) throw new Error(json.error || 'Ukendt fejl fra Railway');
}

// ── SEND FIL TIL DASHBOARD ────────────────────────────────────────────────────
function sendDashboard(filBytes, filnavn, mailDato) {
  var base64 = Utilities.base64Encode(filBytes);

  var payload = {
    secret:  WEBHOOK_SECRET,
    filnavn: filnavn,
    data:    base64,
    dato:    mailDato
  };

  var options = {
    method:             'POST',
    contentType:        'application/json',
    payload:            JSON.stringify(payload),
    muteHttpExceptions: true,
    headers:            { 'X-Webhook-Secret': WEBHOOK_SECRET }
  };

  var svar = UrlFetchApp.fetch(DASHBOARD_URL, options);
  Logger.log('Dashboard svar: ' + svar.getResponseCode() + ' - ' + svar.getContentText());
  // Kaster ikke fejl — bestillingsappen påvirkes ikke af dashboard-fejl
}

// ── TEST FORBINDELSE TIL RAILWAY ──────────────────────────────────────────────
function testForbindelse() {
  var options = {
    method:             'GET',
    muteHttpExceptions: true,
    headers:            { 'X-Webhook-Secret': WEBHOOK_SECRET }
  };
  var svar = UrlFetchApp.fetch(
    'https://bestilling-app-production.up.railway.app/api/rapport-status',
    options
  );
  Logger.log('Bestillingsapp: ' + svar.getResponseCode() + ' - ' + svar.getContentText());

  var svar2 = UrlFetchApp.fetch(
    'https://om-dashboard-production-0f3a.up.railway.app/api/rapport-status',
    options
  );
  Logger.log('Dashboard: ' + svar2.getResponseCode() + ' - ' + svar2.getContentText());
}

// ── TEST MED SENESTE MAIL ─────────────────────────────────────────────────────
function testMedSenesteMail() {
  var tråde = GmailApp.search(SØGEORD, 0, 1);

  if (tråde.length === 0) {
    Logger.log('Ingen mails fundet med søgeord: ' + SØGEORD);
    return;
  }

  var besked = tråde[0].getMessages().slice(-1)[0];
  var krop   = besked.getPlainBody() + ' ' + besked.getBody();
  var link   = findShopboxLink(krop);

  Logger.log('=== TEST RESULTAT ===');
  Logger.log('Emne:           ' + besked.getSubject());
  Logger.log('Dato:           ' + besked.getDate());
  Logger.log('Shopbox link:   ' + (link || 'IKKE FUNDET'));
  Logger.log('Vedhæftninger:  ' + besked.getAttachments().length);

  if (link) {
    Logger.log('→ Henter fil fra Shopbox...');
    var response = UrlFetchApp.fetch(link, { muteHttpExceptions: true });
    if (response.getResponseCode() === 200) {
      var filBytes = response.getContent();
      Logger.log('Fil hentet: ' + filBytes.length + ' bytes');

      try {
        sendFilTilRailway(filBytes, 'salgsdata.xlsx', besked.getDate().toISOString());
        Logger.log('✓ Bestillingsapp OK');
      } catch(e) {
        Logger.log('⚠ Bestillingsapp fejl: ' + e.toString());
      }

      try {
        sendDashboard(filBytes, 'salgsdata.xlsx', besked.getDate().toISOString());
        Logger.log('✓ Dashboard OK');
      } catch(e) {
        Logger.log('⚠ Dashboard fejl: ' + e.toString());
      }
    }
  } else if (besked.getAttachments().length > 0) {
    var vedhæftninger = besked.getAttachments();
    for (var v = 0; v < vedhæftninger.length; v++) {
      var filnavn = vedhæftninger[v].getName().toLowerCase();
      if (filnavn.indexOf('.xlsx') >= 0 || filnavn.indexOf('.txt') >= 0 || filnavn.indexOf('varesalg') >= 0) {
        Logger.log('→ Sender vedhæftning: ' + vedhæftninger[v].getName());
        var fb = vedhæftninger[v].getBytes();

        try {
          sendFilTilRailway(fb, 'salgsdata.xlsx', besked.getDate().toISOString());
          Logger.log('✓ Bestillingsapp OK');
        } catch(e) {
          Logger.log('⚠ Bestillingsapp fejl: ' + e.toString());
        }

        try {
          sendDashboard(fb, 'salgsdata.xlsx', besked.getDate().toISOString());
          Logger.log('✓ Dashboard OK');
        } catch(e) {
          Logger.log('⚠ Dashboard fejl: ' + e.toString());
        }
        break;
      }
    }
  } else {
    Logger.log('→ ADVARSEL: Hverken link eller vedhæftning fundet!');
  }
}
