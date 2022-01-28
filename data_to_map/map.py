import math
import os
import re
import time
import json
import sys
import signal
from pathlib import Path
from collections import defaultdict, Counter

# from shapely.geometry import Point, Polygon
import pandas
import geopandas as gpd
import shapely
# from bokeh.io import show, output_file
from bokeh.models import (
    LinearColorMapper,
    Circle,
    Scatter,
    MultiPolygons,
    GeoJSONDataSource,
    HoverTool,
    WheelZoomTool,
    PanTool,
    Panel,
    Tabs,
    WMTSTileSource,
    CustomJS,
    Div,
    CheckboxGroup,
    # CheckboxButtonGroup,
    # MultiChoice,
    Button,
    ColumnDataSource,
    TapTool,
    OpenURL,
    Toggle,
    # CustomJSHover,
)
from bokeh import events
from bokeh.layouts import column, row
from bokeh.palettes import Reds256 as palette
from bokeh.plotting import figure
from bokeh.resources import JSResources
from bokeh.io import export_png
from bokeh.embed import (
    # file_html,
    components,
)


# Currently unused, but preserved here for reference.
def lat_lon_to_web_mercator(lon, lat):
    x = lon * 20037508.34 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) / (math.pi / 180)
    y = y * 20037508.34 / 180
    return x, y


def polygon_to_list(poly):
    shape = [list(poly.exterior.coords)]
    shape.extend(list(i.coords) for i in poly.interiors)
    return shape


def multipolygons_to_xs_ys(multipolygons):
    geometries = []
    for m in multipolygons:
        if isinstance(m, shapely.geometry.Polygon):
            m = [m]
        else:
            m = list(m)
        geometries.append(list(map(polygon_to_list, m)))

    geo_xs = [[[[x for x, y in ring_pairs]
                for ring_pairs in polygon]
               for polygon in multipolygon]
              for multipolygon in geometries]
    geo_ys = [[[[y for x, y in ring_pairs]
                for ring_pairs in polygon]
               for polygon in multipolygon]
              for multipolygon in geometries]
    return geo_xs, geo_ys


# If the world were a good place, this function would not be
# needed, and we could pass the geopandas dataframe straight
# to GeoJSONDataSource. That ALMOST works. But for some
# reason, no existing Bokeh glyph understands how to render
# patches with holes in them as represented by shapely Polygons.
# The closest thing is Bokeh's MultiPolygons glyph, but it
# doesn't accept shapely objects or geojson or anything
# like that. Wah wah. So instead we have to do this by hand.
# Also, Infinity isn't a valid JSON value, even though pandas
# seems to think it is.
def geodf_patches_to_geods(geodf):
    geo_xs, geo_ys = multipolygons_to_xs_ys(geodf['geometry'])
    geodf = geodf.assign(xs=geo_xs, ys=geo_ys)
    return GeoJSONDataSource(
        geojson=geodf.to_json().replace('Infinity', 'null')
    )


def safe_lt(comp):
    def comp_func(val):
        try:
            return float(val) < comp
        except ValueError:
            return False
    return comp_func


def can_be_simplified(val, tol=10.0):
    try:
        val.simplify(tol)
        return True
    except (ValueError, AttributeError):
        return False


def country_name_perma(name):
    urlsafe = country_name_urlsafe(name)
    return f'countries/{urlsafe}'


def country_name_urlsafe(name):
    urlsafe = name.lower().replace(' ', '-').replace('\'', '-')
    urlsafe = urlsafe.replace('ô', 'o')  # Côte d'Ivoire causes trouble.
    return urlsafe


def protest_name_perma(name):
    urlsafe = protest_name_urlsafe(name)
    return f'protests/{urlsafe}'


def protest_name_urlsafe(name):
    perma = name.lower().replace(' ', '-').replace('\'', '-')
    return perma


def load_geojson(simplify_tol=None):
    gdf = gpd.read_file('data_to_map/data/gadm28_countries.geojson')
    gdf = gdf[gdf['geometry'].notna()]

    # For all countries, drop just Antarctica:
    # gdf = gdf[gdf['name_engli'] != 'Antarctica']

    # For just africa drop other continents:
    gdf = gdf[gdf['unregion2'] == 'Africa']

    gdf['name'] = gdf['name_engli']
    gdf['perma'] = gdf['name'].apply(country_name_perma)
    gdf = gdf.set_index('name_engli')

    # Project from lat, lon data to web mercator.
    gdf = gdf.to_crs('EPSG:3857')

    # Use shapely simplification routine if simplify_tol is specified.
    if simplify_tol is not None:
        gdf = gdf[gdf['geometry'].apply(can_be_simplified)]
        gdf['geometry'] = gdf['geometry'].simplify(
            simplify_tol,
            preserve_topology=False
        )
    return gdf


def load_protests():
    protests = pandas.read_csv('data_to_map/data/protests.csv')
    protests_wrong_data = protests[
        # protests.LONG.apply(safe_lt(-20)) |
        protests.LONG.isna() |
        protests.LAT.isna()
    ]

    print('len(protests_wrong_data)', len(protests_wrong_data))
    print('protests_wrong_data.LAT.head()', protests_wrong_data.LAT.head())
    print('protests_wrong_data.LONG.head()', protests_wrong_data.LONG.head())

    protests = protests.drop(protests_wrong_data.index, axis='rows')

    protests['LAT'] = protests.LAT.apply(float)
    protests['LONG'] = protests.LONG.apply(float)

    print('len(set(protests[unique-key]))',
          len(set(protests['unique-key'])))
    print('len(protests)', len(protests))
    assert len(set(protests['unique-key'])) == len(protests)

    protests['perma'] = protests['unique-key'].apply(protest_name_perma)
    protests = protests.set_index('unique-key')

    protests = gpd.GeoDataFrame(
        protests,
        geometry=gpd.points_from_xy(protests.LONG, protests.LAT),
        crs='epsg:4326'  # CRS code for basic lat/lon data.
    )
    protests = protests.to_crs('EPSG:3857')  # CRS code for web mercator.

    return protests


_name_errors = {
    'Madagascar ': 'Madagascar',
    "Cote d'lvoire": "Côte d'Ivoire",
    'Djbouti': 'Djibouti',
    'Malawi ': 'Malawi',
    'Mauritus': 'Mauritius',
    'Mauritania ': 'Mauritania',
    'Congo- Brazzaville': 'Republic of Congo',
    'Congo - Kinshasa': 'Democratic Republic of the Congo',
    'Guinea Bissau': 'Guinea-Bissau'
}


def sum_protests(protests, countries):
    counts = defaultdict(int)

    names = [_name_errors[n] if n in _name_errors else n
             for n in protests['Country Name']]
    counts = Counter(names)

    # print(set(counts) - set(countries['name']))
    # print(set(countries['name']) - set(counts))

    countries['protestcount'] = [counts[n] for n in countries['name']]

    country_rank = sorted(set(counts.values()), reverse=True)
    country_rank.append(0)
    country_rank = {c: i for i, c in enumerate(country_rank)}
    country_rank = {n: country_rank[counts[n]] for n in countries['name']}
    countries['rank'] = [country_rank[n] for n in countries['name']]


def base_map(tile_url, tile_attribution='MapTiler',
             zoomable=False, draggable=False):
    # Plot
    p = figure(
        title="",
        plot_width=600,
        plot_height=700,
        x_axis_location=None, y_axis_location=None,
        y_range=(-4246229, 4715858),
        x_range=(-2054627, 5752956),
        x_axis_type="mercator", y_axis_type="mercator",
        # sizing_mode="scale_both"
        )

    if zoomable:
        zoom = WheelZoomTool()
        p.add_tools(zoom)
        p.toolbar.active_scroll = zoom

    if draggable:
        drag = PanTool()
        p.add_tools(drag)
        p.toolbar.active_drag = drag

    p.toolbar_location = None
    p.grid.grid_line_color = None

    p.add_tile(WMTSTileSource(
        url=tile_url,
        attribution=tile_attribution
    ))

    return p


# ***
def individual_point_map(
        point_source, selected_ix,
        x_range, y_range,
        tile_url, tile_attribution='MapTiler',
        ):

    plot = figure(
        title="",
        plot_width=600, plot_height=700,
        x_axis_location=None, y_axis_location=None,
        y_range=y_range,
        x_range=x_range,
        x_axis_type="mercator", y_axis_type="mercator",
        # sizing_mode="scale_width"
        )

    plot.toolbar_location = None
    plot.grid.grid_line_color = None

    plot.add_tile(WMTSTileSource(
        url=tile_url,
        attribution=tile_attribution
    ))

    point = Scatter(
        marker="circle",
        x='x', y='y', fill_color="purple", fill_alpha=0.5,
        line_color="purple", line_alpha=0.5, size=12, name="points_scatter")

    selection_point = Scatter(
        marker="star",
        x='x', y='y', fill_color="purple", fill_alpha=0.8, line_width=7,
        line_color="red", line_alpha=0.5, size=12,
        name="selection_points_scatter")

    point_source.selected.indices = [selected_ix]

    plot.add_glyph(point_source,
                   point,
                   hover_glyph=selection_point,
                   selection_glyph=selection_point,
                   name="points_renderer")

    return plot


def patches(plot, div, patch_data):
    color_mapper = LinearColorMapper(palette=palette)
    patches = MultiPolygons(
        xs='xs', ys='ys',
        fill_color={'field': 'rank', 'transform': color_mapper},
        fill_alpha=0.5, line_color="black", line_alpha=0.2,
        line_width=2.5
    )
    hover_patches = MultiPolygons(
        xs='xs', ys='ys',
        fill_color={'field': 'rank', 'transform': color_mapper},
        fill_alpha=0.5, line_color="black", line_alpha=0.5,
        line_width=3.5
    )
    patch_source = geodf_patches_to_geods(patch_data)
    render = plot.add_glyph(patch_source,
                            patches,
                            hover_glyph=hover_patches,
                            selection_glyph=patches,
                            nonselection_glyph=patches)

    parsed_geojson = json.loads(patch_source.geojson)

    # str.source.selected.indices gives you a list of things that you
    # immediately clicked on

    # ##################################################################
    #                                                                  #
    #   NOTE: Indented code will be formatted by the markdown engine   #
    #         into a preformatted code block for display, instead of   #
    #         being inserted as HTML/JavaScript. So don't indent!      #
    #                                                                  #
    # ##################################################################
    #
    #         To further elaborate...
    #
    #         I (Scott) don't fully understand what's happening here.
    #         However, I observed that this JavaScript code was being
    #         escaped in the page source code -- it was appearing,
    #         but in a non-JavaScripty way, with lots of HTML
    #         entities.
    #
    #         The only explanation that readily occurs to me is that
    #         the Jekyll markdown engine is seeing that the code is
    #         indented, and deciding that it is supposed to be
    #         rendered as a preformatted code block instead of as
    #         working JavaScript. And indeed, when I removed the
    #         indentation, the page went back to working as
    #         expected. So... don't indent this... I guess?
    #

    code = """
var features = json_source['features'];
var properties = features[cb_data.index.indices[0]];
if (properties != undefined) {
    var rank = properties['properties']['rank'] + 1;
    var name = properties['properties']['name'];
    var protestcount = properties['properties']['protestcount'];
}
"""

    callback = CustomJS(
        args=dict(json_source=parsed_geojson, div=div),
        code=code
    )

    hover = HoverTool(
        tooltips=None,
        renderers=[render],
        point_policy="follow_mouse",
        callback=callback
    )
    plot.add_tools(hover)
    plot.toolbar.active_inspect = hover

    tap = TapTool(
        renderers=[render],
        callback=OpenURL(
            url='/@perma'
        )
    )
    plot.add_tools(tap)

    return plot

def points(plot, div, point_source):
    point = Scatter(
        marker="circle",
        x='x', y='y', fill_color="purple", fill_alpha=0.5,
        line_color="purple", line_alpha=0.5, size=6, name="points")

    hover_point = Scatter(
        marker="star",
        x='x', y='y', fill_color="purple", fill_alpha=0.8, line_width=5,
        line_color="red", line_alpha=0.5, size=6, name="hover_points")

    circle_renderer = plot.add_glyph(point_source,
                                     point,
                                     hover_glyph=hover_point,
                                     selection_glyph=point,
                                     name="points")

    hover_callback = CustomJS(args=dict(source=point_source, div=div),
                              code="""

        // `features` contains *all* the points that have been identified
        // by the filters on the left side of the map.
        var features = source['data'];

        // `indices` contains the indices of those points currently
        // being hovered over on the map.
        var indices = cb_data.index.indices;

        if (indices.length != 0) {
            div.text = "<div style='background-color:lightgray; " +
                       "height:650px; padding:10px; overflow: scroll'>" +
                       "<h3 style='color:gray'>" + "NUMBER OF PROTESTS: " +
                       indices.length + "</h3>" + "<br>"

            for (var i = 0; i < indices.length; i++) {
                var protest = indices[i];
                var desc = features['Description of Protest'][protest];
                var uni = features['School Name'][protest]
                    .toString()
                    .toUpperCase();
                var type = features['Event Type (F3)'][protest];
                var date = features['Date'][protest];
                var locationName = features['Locality Name'][protest];

                // `baseurlPrefix` will need to match whatever
                // baseurl Jekyll's _config.yml file specifies...
                // pretty awkward, but I see no obvious alternative.
                //var baseurlPrefix = '/'

                var protestName = "/" + features['perma'][protest];

                // In the following I tried to make the underlying HTML
                // structure a little more visible. It's not perfect,
                // but I hope it helps a bit.


                var location = features['Protest Location (F2)'][protest];

                div.text += '<a class ="spa-protest-result" target="_blank" href="' + protestName +'">'+'<section class="spa-category-button" style="background-color:white;argin:10px; padding-left:5px">'
                + '<p style="padding:3px; display:inline-block; color:gray; font-size:15px">' +'<i class="fa fa-globe-africa" style="padding:3px">'+'</i>'+
                        " " + uni + '</p>' + '<div style="font-weight: bold; padding:3px; display:inline-block; border-radius:4px">' + date +'</div>' + '<br  >'
                + '<div style="padding-left:5px; padding-right:5px">' + desc + '</div>' + '<div style="background-color:#F7D9FA; padding:3px; display:inline-  block; border-radius:4px">' + type +'</div>'
                + '<div style="background-color:#ccffff; padding:3px; display:inline-block; border-radius:4px">' + location +'</div>'
                         + '<br>' + '</section>' + '</a>' + '<br>';
                }
          }  """)


    hover = HoverTool(
        tooltips=None,
        point_policy="follow_mouse",
        renderers=[circle_renderer],
        callback=hover_callback
    )

    plot.add_tools(hover)
    plot.toolbar.active_inspect = hover


def filter_values(protest_col):
    # Some values are comma-separated lists. Drop null values, split lists,
    # flatten them out, and deduplicate.
    return set(val.strip()
               for val_list in protest_col if not pandas.isnull(val_list)
               for val in val_list.split(','))


def toggle(filter_col, filter):
    title = re.sub(r'\s*[(]F[0-9]+[)]\s*', '', filter_col)
    class_select = title.replace(" ", "")
    title = title.upper()
    select_toggle = Toggle(
        label=title+"  ",
        width=175,
        name=class_select,
        active=True,
        css_classes=[class_select, "bk-checkbox"]
        )

    select_toggle.js_link('active', filter, 'visible')

    return select_toggle

def filter_name_camel(filter_name):
    name = filter_name_clean(filter_name)
    return name.replace(" ", "")


def filter_name_clean(filter_name):
    return re.sub(r'\s*[(]F[0-9]+[)]\s*', '', filter_name)


def one_filter(plot, filter_col, filter_vals, filters_state,
               hidden_button, max_items):
    # Remove (FX) from column name; probaby temporary
    title = filter_name_camel(filter_col)

    options = list(filter_vals)
    multi_select = CheckboxGroup(
        labels=options,
        css_classes=[title, "bk-checkbox"],
        default_size=150,
        height_policy='min',
        visible=True
    )

    # The state of each multi-select is stored in an intermediate
    # table; when a given multi-select is changed, the table is updated.
    # The change to the table triggers a second update, defined below
    # in `point_plot`. That second update modifies the map based on
    # the state of all multi-selects simultaneously. This way, individual
    # multi-select widgets can operate independently without knowing
    # anything about one another.

    multi_select.js_on_change('active', CustomJS(
        args=dict(filter_col=filter_col,
                  filters_state=filters_state),
        code="""
            // Turn the list of active indices into a list of labels.
            let select_vals = this.active.map((act) => this.labels[act]);

            let state_col = filters_state.data[filter_col];

            for (let i = 0; i < state_col.length; i++) {
                if (i < select_vals.length) {
                    state_col[i] = select_vals[i];
                } else {
                    state_col[i] = '';
                }
            }
            filters_state.properties.data.change.emit();
        """)
    )

    name = f'callback-load-hash-filter-{title}'
    # print(name)
    hidden_button.js_on_event(events.ButtonClick, CustomJS(
        name=name,
        args=dict(filter=multi_select, filterName=title),
        code="""
            let active = window.location.hash.slice(1).split(',')
                .filter(a => a.startsWith(filterName))
                .map(a => a.replace(filterName + '-', ''))
                .map(a => a.replaceAll('+', ' '));

            let activeMap = new Map();
            for (let i = 0; i < filter.labels.length; i++) {
                activeMap.set(filter.labels[i], i);
            }

            filter.active.splice(0);
            for (const a of active) {
                filter.active.push(activeMap.get(a));
            }

            // Force the widget to re-rennder. Otherwise
            // the checkbox does not update.
            filter.visible = false;
            filter.visible = true;

            // Announce that the active filters have changed.
            filter.properties.active.change.emit();
        """)
    )
    return multi_select


class Map:
    def __init__(self):
        self.protests = load_protests()
        self.countries = load_geojson()
        self.filters = self.collect_filters()
        sum_protests(self.protests, self.countries)

    def collect_filters(self):
        """
        Go through the protest CSV columns, identify the ones with an
        (F[n]) annotation at the end, and return a list of just those column
        names, sorted by the number n.
        """

        cols = self.protests.columns
        filters = [f for f in cols if re.search(r'\s*[(]F[0-9]+[)]\s*', f)]
        digits = [int(re.search(r'\s*[(]F(?P<n>[0-9]+)[)]\s*', f)['n'])
                  for f in filters]
        filters = [f for d, f in sorted(zip(digits, filters))]

        return {f: filter_values(self.protests[f]) for f in filters}

    def patch_plot(self, tile_url, tile_attribution='MapTiler'):
        plot = base_map(tile_url, tile_attribution)
        div = Div(width=plot.plot_width // 2,
                  height=plot.plot_height,
                  height_policy="fixed",
                      text='<div class="spa-centered">' +
                      '<h1 class="header-large">' + '<span class="anim" style="opacity: 0; animation: textanim2 5s">' +
                      'Documenting'+'</span>'+'<br>'+ '<span class="anim" style="opacity: 0; animation: textanim2 10s">'+'Amplifying'+ '</span>'+'<br>'+'<span style="animation: textanim 15s">'+'Mapping'+'</span>'+'<br>'+'Contemporary School Protests' +'<br>'+'in Africa' + '</h1>'
            + '<p class="spa-large-p">' +
            'African schools, and students in particular, have historically played a progressive role in anti-colonial, anti-imperial, and pro-democracy movements. Today, Africa is witnessing a resurgence of educational activism and youth-led popular struggles. The' + ' <b>'+' School Protests in Africa'+' </b>'+'digital project documents the incidence and causes of school-based protests in Africa since 2000, demonstrating the continued importance of schools and school actors in political participation and processes of social change in Africa.' + '</p>'
            +'<p class="spa-large-p hidden-on-mobile" style="font-size:18px; font-weight:bold; text-align:left">' + 'Click on a region to begin.' + '</p>' +'</p>'
            + '</div>'
            )

        patches(plot, div, self.countries)

        hash_callback = CustomJS(
            name="callback-load-hash-coordinates-country",
            args=dict(x=plot.x_range, y=plot.y_range),
            code="""
                console.log([x.start, x.end, y.start, y.end].join(','))
                let data = window.location.hash.slice(1)
                                 .split(',').map(x => +x);
                if (data.length == 4 && data.every(x => !isNaN(x))) {
                    [x.start, x.end, y.start, y.end] = data;
                }
                console.log(self);
            """
        )
        hidden_button = Button(label="Reset Zoom",
                               button_type="success",
                               visible=False)
        hidden_button.js_on_event(events.ButtonClick, hash_callback)

        patches_layout = row(div, plot)
        button_layout = column(hidden_button, patches_layout)
        return button_layout

    def point_plot(self, tile_url, tile_attribution='MapTiler'):
        plot = base_map(tile_url, tile_attribution)

        div = Div(width=plot.plot_width // 2,
                  height=plot.plot_height,
                  height_policy="fixed",
                  css_classes = ["desktop-results"],
                  text="<div style='background-color:lightgray; height:650px; padding:10px; overflow: scroll'>" +
                       "<h3 style='color:gray'>" + "Use filters to the left to display protests based on category. Hover over protests on map for more information." +
                        "</h3>" + "<br>")

        mobile_div = Div(width=plot.plot_width // 2,
                  height=plot.plot_height,
                  height_policy="fixed",
                  css_classes = ["mobile-results"],
                  text="<div style='background-color:lightgray; height:400px; padding:10px; overflow: scroll'>" +
                       "<h3 style='color:lightgray'>" + "Use filters to display protests based on category." +
                        "</h3>" + "<br>")


        search_button = Button(label="Filter protests",
                                button_type="success",
                                visible=True,
                                css_classes = ["mobile-button"])

        # Create two copies of the protest data. One will be the data to be
        # displayed, and will be mutable. The other will be an unchanging
        # collection of all the data. Upon a filter change, the data to
        # be displayed is emptied and filled with a subset of the full data.
        protests_json = self.protests.to_json()
        full_source = GeoJSONDataSource(geojson=protests_json)
        point_source = GeoJSONDataSource(geojson=protests_json)


        # Here, point_source, which contains just the selected points,
        # gets "attached" to the map and the hover div.

        points(plot, div, point_source)

        search_button.js_on_event(events.ButtonClick, CustomJS(
            args=dict(div=mobile_div, point_source=point_source, button=search_button),
                code=""" 

                    div.text = "<div style='background-color:lightgray; height:400px; padding:10px; overflow: scroll'>" +
                       "<h3 style='color:gray'>" +
                        "</h3>" + "<br>";
                    let features = point_source['data'];
                    let length;

                    console.log(features['School Name'].length);
                    if(features['School Name'].length > 10 | features['School Name'].length == 10){
                        length = 10;
                    }else if(features['School Name'].length < 10){
                        length = features['School Name'].length
                    }

                    for (let i = 0; i < length; i++) {
                    var desc = features['Description of Protest'][i];
                    var uni = features['School Name'][i]
                        .toString()
                        .toUpperCase();
                    var type = features['Event Type (F3)'][i];
                    var date = features['Date'][i];
                    var locationName = features['Locality Name'][i];

                    // `baseurlPrefix` will need to match whatever
                    // baseurl Jekyll's _config.yml file specifies...
                    // pretty awkward, but I see no obvious alternative.
                    //zoop
                    //var baseurlPrefix = '/';

                    var protestName = "/" + features['perma'][i];

                    // In the following I tried to make the underlying HTML
                    // structure a little more visible. It's not perfect,
                    // but I hope it helps a bit.
                    //doop da doo

                    div.text +=
    '<a href="' + protestName + '">' +
      '<section style="background-color:white; margin:10px; padding-left:5px">' +
        '<p style="padding:3px; display:inline-block; color:gray; font-size:15px">' +
          '<i class="fa fa-globe-africa" style="padding:3px">' +
          '</i>' + " " +
          uni +
        '</p>' +
        '<div style="font-weight: bold; padding:3px; display:inline-block; border-radius:4px">' +
          date +
        '</div>' + '<br>' +
        '<div style="padding-left:5px; padding-right:5px">' +
          desc +
        '</div>' +
        '<div style="background-color:#F7D9FA; padding:3px; display:inline-block; border-radius:4px">' +
          type +
        '</div>' +
        '<div style="background-color:#ccffff; padding:3px; display:inline-block; border-radius:4px">' +
          locationName +
        '</div>' + '<br>' +
      '</section>' +
    '</a>'+ '<br>';

            }
            if (features['School Name'].length > 10){
                div.text += "Additional protests for this search may not be shown; please see the desktop version of the site for full results";
                }        

                """))


        hash_callback = CustomJS(
                name="callback-load-hash-coordinates-protests",
                args=dict(x=plot.x_range, y=plot.y_range),
                code="""
                    console.log([x.start, x.end, y.start, y.end].join(','))
                    let data = window.location.hash.slice(1)
                                     .split(',').map(x => +x);
                    if (data.length == 4 && data.every(x => !isNaN(x))) {
                        [x.start, x.end, y.start, y.end] = data;
                    }
                """
            )
        hidden_button = Button(label="Reset Zoom",
                               button_type="success",
                               visible=False)
        hidden_button.js_on_event(events.ButtonClick, hash_callback)

        # The number of items is different for different filters, but
        # they are stored in a table that must have the same number of
        # items in each column, so we pad the columns with empty strings.
        max_items = max(len(v) for v in self.filters.values())

        # The filters will modify the points displayed on the map, but
        # they will do so indirectly. They will modify the content of
        # filters_state via their callbacks. *Then*, whenever filters_state
        # is changed, it will modify the point_source based on its knowledge
        # of the current state of all the filters at once. This way,
        # the filters don't have to pay any attention to each other; their
        # interaction is managed entirely by the filters_state object, via
        # this callback.
        filters_state = ColumnDataSource(pandas.DataFrame({
            col: [''] * max_items for col in self.filters
        }))
        filters_state.js_on_change('data', CustomJS(
            args=dict(point_source=point_source,
                      full_source=full_source),
            code="""
            let filters_state = cb_obj.data;

            // A given protest can have multiple tags separated by commas.
            let unpackVals = function(vals) {
                vals = vals ? vals.split(',').map(s => s.trim()) : [];
                return new Set(vals);
            };

            // Do any of the selected tags match any of the protest tags?
            let selectionMatch = function(selections, vals) {
                vals = unpackVals(vals);
                selections = new Set(selections);
                selections.delete('');

                // If no selections have been made, it's a match.
                if (selections.size === 0) {
                    return true;
                }

                // If there is any intersection, it's a match.
                for (const sel of selections) {
                    if (vals.has(sel)) {
                        return true;
                    }
                }
                return false;
            };

            // Get the indices of the protests accepted by all filters.
            let filterIndices = function(filters_state, full_source) {
                let cols = Object.keys(filters_state);
                let nrows = full_source.data[cols[0]].length;
                let indices = [];

                for (let i = 0; i < nrows; i++) {
                    let accept = true;
                    for (const col of cols) {
                        if (col === 'index') { continue; }
                        let selections = filters_state[col];
                        let vals = full_source.data[col][i];
                        if (!selectionMatch(selections, vals)) {
                            accept = false;
                            break;
                        }
                    }
                    if (accept) {
                        indices.push(i);
                    }
                }
                return indices;
            };

            // Empty out the point_source data.
            for (const [column, values] of Object.entries(point_source.data)) {
                while (values.length > 0) {
                    values.pop();
                }
            }

            // Refill the point_source data based on the current filter state.
            let indices = filterIndices(filters_state, full_source);
            for (const [column, values] of Object.entries(full_source.data)) {
                for (const i of indices) {
                    point_source.data[column].push(values[i]);
                }
            }

            point_source.change.emit();
            """))

        duo_stack = []
        for filter_name, filter_vals in self.filters.items():
            filter = one_filter(plot, filter_name, filter_vals,
                                filters_state, hidden_button, max_items)
            tog = toggle(filter_name, filter)
            duo_stack.append(tog)
            duo_stack.append(filter)
        duo_col = column(*duo_stack)
        duo_col.css_classes = ["spa-filters-column"]

        results_col = column(search_button,mobile_div)
        map_select = row(duo_col, plot, div)
        layout = column(hidden_button, map_select, results_col)
        return layout

    def individual_point_plots(
            self, tile_url, tile_attribution='MapTiler'
            ):

        protests_json = self.protests.to_json()
        point_source = GeoJSONDataSource(geojson=protests_json)

        for selected_ix in range(len(self.protests)):
            geo = self.protests.iloc[selected_ix].geometry.coords[0]
            point_x, point_y = geo

            width = 5000
            x_range = (point_x - width, point_x + width)
            y_range = (point_y - width, point_y + width)

            plot = individual_point_map(
                point_source, selected_ix,
                x_range, y_range,
                tile_url, tile_attribution
                )
            path = 'docs/assets/img/protest-points'
            export_png(plot, filename=f'{path}/protest_{selected_ix}.png')

    def country_pages(self, path):
        for i, name in enumerate(sorted(self.countries.index.values)):
            urlsafe = country_name_urlsafe(name)
            perma = country_name_perma(name)
            filename = (Path(path) / Path(urlsafe)).with_suffix('.md')
            title = name
            with open(filename, 'w', encoding='utf-8') as op:
                op.write(f'---\n'
                         f'layout: country\n'
                         f'row_index: {i}\n'
                         f'permalink: {perma}\n'
                         f'title: {title}\n'
                         f'hidden: true\n'
                         f'---\n')

    def protest_pages(self, path):
        for i, row_ix in enumerate(self.protests.index.values):
            if i > 7000:
                break
            row = self.protests.loc[row_ix]

            perma = row['perma']

            filename = protest_name_urlsafe(row_ix)
            filename = (Path(path) / Path(filename)).with_suffix('.md')

            title = row['Protest Name']
            title = title.split('-')
            title = f'{title[0]}, {title[1]} {title[2]}'

            with open(filename, 'w', encoding='utf-8') as op:
                op.write(f'---\n'
                         f'layout: protest\n'
                         f'row_index: {i}\n'
                         f'permalink: {perma}\n'
                         f'title: {title}\n'
                         f'hidden: true\n'
                         f'---\n')


def save_embeds(include_path, tab_plot, patch_plot, point_plot, filters):
    country_cb = ['callback-load-hash-coordinates-country']
    protest_cb = ['callback-load-hash-coordinates-protests']

    filter_cb = ['callback-load-hash-filter-' + filter_name_camel(f)
                 for f in filters]

    include_path = Path(include_path)

    with open(include_path / "map.html", 'w', encoding='utf-8') as op:
        save_onload_callback(op, country_cb + protest_cb + filter_cb)
        save_components(tab_plot, op)

    with open(include_path / "country-map.html", 'w', encoding='utf-8') as op:
        save_onload_callback(op, country_cb + filter_cb)
        save_components(patch_plot, op)

    with open(include_path / "protest-map.html", 'w', encoding='utf-8') as op:
        save_onload_callback(op, protest_cb + filter_cb)
        save_components(point_plot, op)

    # This ensures that the right version of BokehJS is always in use
    # on the jekyll site.
    with open(include_path / 'bokeh_heading.html',
              'w', encoding='utf-8') as op:
        save_script_tags(op)


def save_html(tab_plot, patch_plot, point_plot, filters):
    country_cb = ['callback-load-hash-coordinates-country']
    protest_cb = ['callback-load-hash-coordinates-protests']

    filter_cb = ['callback-load-hash-filter-' + filter_name_camel(f)
                 for f in filters]

    with open("map-tab-standalone.html", 'w', encoding='utf-8') as op:
        op.write("""
        <!DOCTYPE html>
        <html lang="en">
        """)

        save_script_tags(op)
        save_components(tab_plot, op)
        save_onload_callback(op, country_cb + protest_cb + filter_cb)

        op.write("""
        <div id="map-hover-context">
        </div>
        </html>
        """)

    with open("map-country-standalone.html", 'w', encoding='utf-8') as op:
        op.write("""
        <!DOCTYPE html>
        <html lang="en">
        """)

        save_script_tags(op)
        save_components(patch_plot, op)
        save_onload_callback(op, country_cb + filter_cb)

        op.write("""
        <div id="map-hover-context">
        </div>
        </html>
        """)

    with open("map-protest-standalone.html", 'w', encoding='utf-8') as op:
        op.write("""
        <!DOCTYPE html>
        <html lang="en">
        """)

        save_script_tags(op)
        save_components(point_plot, op)
        save_onload_callback(op, protest_cb + filter_cb)

        op.write("""
        <div id="map-hover-context">
        </div>
        </html>
        """)


def save_script_tags(open_file):
    # This loads more JS files than is strictly necessary. We really only
    # need the main bokeh file and the widgets file. But it's not yet clear
    # that the gain in loading time is worth the extra complexity of weeding
    # out the other files.
    for f in JSResources(mode='cdn').js_files:
        open_file.write(
            f'<script type="text/javascript" src="{f}" '
            'crossorigin="anonymous"></script>\n'
        )

    open_file.write(
        '<script type="text/javascript"> \n'
        '    Bokeh.set_log_level("info"); \n'
        '</script>\n'
    )


def save_components(plot, open_file):
    for c in components(plot):
        open_file.write(c)
        open_file.write('\n')


def save_onload_callback(open_file, callback_names):
    """
    Create a callback that sets an interval to call a function
    after some delay. The function causes the Bokeh map to zoom
    to a particular set of coordinates. The delay starts at 16
    milliseconds, so that if the Boekh document loads quicky,
    we snappily zoom to the correct location. Otherwise, the
    interval is reset. But we don't want to bog the browser down
    with busy-waiting for the document to load, especially if
    it is broken and will never load! So we double the delay each
    time -- this is called exponential backoff, and it's closely
    related to exponential search. I think if anyone learns any
    computer-science-related thing, it should be this! It's a
    useful stratgey in all kinds of situations, big and small.

    It has the very nice property that the total time spent waiting
    is never more than twice what it absolutely must be, while
    guaranteeing that the amount of time we spend uselessly
    checking for an update is proportional to the logarithm
    of the total time spent waiting -- which is, relatively
    speaking, a very, very small number.

    For example, if we wind up waiting 2 ** 14 milliseconds,
    or about sixteen seconds, we only check to see what's with
    the Bokeh document fourteen times! (Actually, it winds up
    being ten times, since we start at 2 ** 4 = 16 milliseconds.)
    If we just kept on checking every 16 milliseconds, we'd wind
    up checking more than a thousand times! So we really cut down
    on the amount of spammy work we make the browser do without
    sacrificing all that much usability.

    We could adjust that balance by using a floating point number
    less than 2 and greater than 1. 1.25 would lead us to check
    about thirty times in 16 seconds. In 16 minutes, it would
    lead us to check only about fifty times.
    """

    # NOTE: Indented code will be formatted by the markdown engine
    #       into a preformatted code block for display, instead of
    #       being inserted as HTML/JavaScript. So don't indent!
    #
    #       See the comments above, around line 350, for more
    #       explanation.

    # ALSO: I (Scott) have realized that this might be a ridiculous way of
    #       doing this. This should probably just be in a standalone JS file?
    #       Or, if not, I should do some work to understand why. Perhaps
    #       this script needs information that only map.py has when it
    #       is being run? Even then, though, we could save this to a
    #       separate js file and load that file via a script tag
    #       in layouts? But still not 100% sure -- more thought needed.

    open_file.write("""
<script type="text/javascript">
  (function() {

    // A function that accepts an array of bokeh callback names, and
    // executes all of them.
    let execBokehCallbacks = function(callbackNames) {
      for (const cbn of callbackNames) {
        let bokehCallback = window.Bokeh.documents[0].get_model_by_name(cbn);
        if (bokehCallback !== null) {
          bokehCallback.execute();
        }
      }
    };

    window.addEventListener('DOMContentLoaded', function(event) {
      let delay = 16;
      console.log('delay ', delay);
      let intervalfunc = function() {
        window.clearInterval(checkfunc);
        console.log('delay ', delay);
        if (window.Bokeh && window.Bokeh.documents.length > 0) {
          const callbackNames = ['""" +
                    "', '".join(callback_names) +
                    """'];
          execBokehCallbacks(callbackNames);
        } else if (delay < 2 ** 20) {
          delay = delay * 2;
          checkfunc = window.setInterval(intervalfunc, delay);
          console.log('resetting interval with delay ', delay);
        }
      };
      let checkfunc = window.setInterval(intervalfunc, delay);
    });

  })();
</script>
    """)


def main(embed=True, export_point_pngs=False):
    patch_key = ('https://api.maptiler.com/maps/voyager/{z}/{x}/{y}.png?'
                 'key=k3o6yW6gLuLZpwLM3ecn')
    point_key = ('https://api.maptiler.com/maps/outdoor/{z}/{x}/{y}.png?'
                 'key=k3o6yW6gLuLZpwLM3ecn')

    map = Map()

    if export_point_pngs:
        map.individual_point_plots(point_key)
    else:
        patch_vis = map.patch_plot(patch_key)
        point_vis = map.point_plot(point_key)
        tab_vis = Tabs(tabs=[Panel(child=patch_vis, title="Country View"),
                             Panel(child=point_vis, title="Protest View")])

        if embed:
            # The top-level directory for our jekyll site is "docs" so that
            # github pages can build (most of) the site.
            map.country_pages('docs/_countries')
            map.protest_pages('docs/_protests')
            save_embeds('docs/_includes',
                        tab_vis, patch_vis, point_vis,
                        list(map.filters.keys()))

            # Force index and protest map to re-render.
            # Not sure this actually works.
            Path('docs/index.markdown').touch()
            Path('docs/full-protest-map.markdown').touch()
            Path('docs/_includes/bokeh_heading.html').touch()
            Path('docs/_includes/country-map.html').touch()
            Path('docs/_includes/protest-map.html').touch()
        else:
            save_html(tab_vis, patch_vis, point_vis, list(map.filters.keys()))


if __name__ == "__main__":

    if '--standalone' in sys.argv[1:]:
        print("Generating standalone map...")
        main(embed=False)
    elif '--export-point-pngs' in sys.argv[1:]:
        print("Generating point pngs")
        main(export_point_pngs=True)
    else:
        # Get the default signal handler for SIGTERM (see below)
        default_sigterm = signal.getsignal(signal.SIGTERM)

        # We set these variables to keep track of changes
        last_mod_time = 0
        new_mod_time = 0
        init = True
        print("Watching input directory for changes every ten seconds.")
        while True:
            data_files = list(Path("data_to_map/data").iterdir())
            data_files.append(Path(__file__).resolve())
            for data_file in data_files:
                mod_time = os.path.getmtime(data_file)
                if mod_time > new_mod_time:
                    new_mod_time = mod_time

            if init:
                init = False
                last_mod_time = new_mod_time

            if new_mod_time > last_mod_time:
                last_mod_time = new_mod_time
                print("Change detected, generating new map...")
                main()
                print("Map generation complete.")
                print("Watching for changes...")

            # Listen for SIGTERM from docker while sleeping.
            signal.signal(signal.SIGTERM, lambda sig, frame: sys.exit(0))
            time.sleep(10)
            # Ignore SIGTERM while working.
            signal.signal(signal.SIGTERM, default_sigterm)
            # foo
