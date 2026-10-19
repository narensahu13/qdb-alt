"""Made-up sanctions files in each list's real format, and a stand-in server.

Names are invented. One entity, "Northwind Plastic Industries", is on every
list under slightly different spellings, so a customer of that name must hit
all five; individuals, vessels and aircraft are there too and must be ignored.
"""

from __future__ import annotations

from sanctions_lists import fetch

EOF = "\x1a"

UN = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<CONSOLIDATED_LIST>
<INDIVIDUALS><INDIVIDUAL><FIRST_NAME>JOHN</FIRST_NAME><SECOND_NAME>EXAMPLE</SECOND_NAME></INDIVIDUAL></INDIVIDUALS>
<ENTITIES>
<ENTITY><FIRST_NAME>NORTHWIND PLASTIC INDUSTRIES</FIRST_NAME><UN_LIST_TYPE>Al-Qaida</UN_LIST_TYPE>
<REFERENCE_NUMBER>QDe.999</REFERENCE_NUMBER><LISTED_ON>2015-03-01</LISTED_ON>
<ENTITY_ALIAS><QUALITY>a.k.a.</QUALITY><ALIAS_NAME>Northwind Plastics</ALIAS_NAME></ENTITY_ALIAS></ENTITY>
<ENTITY><FIRST_NAME>BLUE HARBOUR TRADING</FIRST_NAME><UN_LIST_TYPE>DPRK</UN_LIST_TYPE>
<REFERENCE_NUMBER>KPe.998</REFERENCE_NUMBER><LISTED_ON>2017-08-05</LISTED_ON></ENTITY>
</ENTITIES>
</CONSOLIDATED_LIST>
"""

SDN = ('36,"NORTHWIND PLASTIC INDUSTRIES W.L.L.",-0- ,"SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \r\n'
       '37,"EXAMPLE, John",individual,"SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \r\n'
       '38,"SEA WIND",vessel,"IRAN",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \r\n' + EOF)
SDN_ALT = '36,1,"aka","NORTHWIND PLASTICS CO.",-0- \r\n' + EOF
CONS = '9640,"NORTHWIND PLASTIC INDUSTRIES",-0- ,"NS-PLC",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \r\n' + EOF
CONS_ALT = EOF

EU = ("fileGenerationDate;Entity_LogicalId;Entity_EU_ReferenceNumber;Entity_DesignationDate;Entity_SubjectType;"
      "Entity_Regulation_Programme;NameAlias_WholeName\n"
      "2026-10-16;101;EU.1.1;2016-01-01;E;TERR;Northwind Plastic Industries\n"
      "2026-10-16;101;EU.1.1;2016-01-01;E;TERR;Northwind Plastics\n"
      "2026-10-16;102;EU.1.2;2016-01-01;P;TERR;John Example\n")

UK = ("Report Date: 16-Oct-2026\n"
      "Last Updated,Unique ID,Name 6,Name 1,Name 2,Regime Name,Designation Type,Date Designated\n"
      "01/01/2026,AQD0999,NORTHWIND PLASTIC INDUSTRIES,,,ISIL (Da'esh) and Al-Qaida,Entity,01/03/2015\n"
      "01/01/2026,AQD0999,NORTHWIND PLASTICS,,,ISIL (Da'esh) and Al-Qaida,Entity,01/03/2015\n"
      "01/01/2026,AQI0001,EXAMPLE,JOHN,,ISIL (Da'esh) and Al-Qaida,Individual,01/03/2015\n")

BODIES = {"un": UN, "ofac_sdn": SDN, "ofac_sdn_alt": SDN_ALT, "ofac_cons": CONS, "ofac_cons_alt": CONS_ALT,
          "eu": EU, "uk": UK}


class Response:
    def __init__(self, status, body: bytes, length=None):
        self.status_code, self.content = status, body
        self.headers = {"Content-Length": str(len(body) if length is None else length)}


class FakeServer:
    def __init__(self):
        self.bodies = dict(BODIES)
        self.cut: set[str] = set()       # keys served cut off (fewer bytes than Content-Length)
        self.down: set[str] = set()

    def get(self, url, headers=None, timeout=None):
        key = next(k for k, (u, *_) in fetch.FILES.items() if u == url)
        if key in self.down:
            return Response(503, b"Service Unavailable")
        body = self.bodies[key].encode("utf-8")
        if key in self.cut:
            return Response(200, body[: len(body) // 2], length=len(body))
        return Response(200, body)
