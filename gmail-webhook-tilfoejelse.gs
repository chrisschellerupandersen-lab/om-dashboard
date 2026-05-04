/**
 * TILFØJ DISSE TO LINJER til toppen af gmail-webhook.gs
 * (lige under den eksisterende RAILWAY_URL konstant)
 *
 * Eksisterende linje (rør ikke ved den):
 *   const RAILWAY_URL = 'https://bestilling-app-production.up.railway.app/api/opdater-rapport';
 *
 * Tilføj denne nye linje nedenunder:
 */
const DASHBOARD_URL = 'https://DIN-DASHBOARD.up.railway.app/api/opdater-rapport';


/**
 * TILFØJ DENNE LINJE i tjekMail() — lige efter den eksisterende sendFilTilRailway() linje
 *
 * Eksisterende kode (rør ikke ved den):
 *   sendFilTilRailway(filBytes, 'salgsdata.xlsx', sidsteBesked.getDate().toISOString());
 *
 * Tilføj denne linje direkte efter:
 */
// sendDashboard(filBytes, 'salgsdata.xlsx', sidsteBesked.getDate().toISOString());
// OG det samme sted i vedhæftnings-blokken:
// sendDashboard(fil.getBytes(), 'salgsdata.xlsx', sidsteBesked.getDate().toISOString());


/**
 * TILFØJ DENNE FUNKTION nederst i gmail-webhook.gs
 * (kopi af sendFilTilRailway men bruger DASHBOARD_URL)
 */
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
  // Kaster ikke fejl — eksisterende bestillingsapp påvirkes ikke af dashboard-fejl
}
