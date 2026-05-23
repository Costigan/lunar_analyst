import proj4 from "proj4";
import Projection from "ol/proj/Projection.js";
import { addEquivalentProjections, addProjection } from "ol/proj.js";
import { register } from "ol/proj/proj4.js";

export type ProjectionConfig = {
  code: string;
  proj4: string;
  extent: [number, number, number, number];
};

function registerAlias(code: string, proj4Def: string, extent: [number, number, number, number]): Projection {
  proj4.defs(code, proj4Def);
  const projection = new Projection({
    code,
    units: "m",
    extent,
    metersPerUnit: 1,
    global: false,
  });
  addProjection(projection);
  return projection;
}

export function registerMapProjection(config: ProjectionConfig): Projection {
  const primary = registerAlias(config.code, config.proj4, config.extent);
  const aliasEpsg103878 = registerAlias("EPSG:103878", config.proj4, config.extent);
  const aliasEsri = registerAlias("urn:ogc:def:crs:ESRI::103878", config.proj4, config.extent);
  const aliasEpsg0 = registerAlias("EPSG::0", config.proj4, config.extent);
  const aliasUrnEpsg0 = registerAlias("urn:ogc:def:crs:EPSG::0", config.proj4, config.extent);
  register(proj4);
  addEquivalentProjections([primary, aliasEpsg103878, aliasEsri, aliasEpsg0, aliasUrnEpsg0]);
  return primary;
}
