---
# Feel free to add content and custom Front Matter to this file.
# To modify the layout, see https://jekyllrb.com/docs/themes/#overriding-theme-defaults

layout: home
title: Protest Map
permalink: /protest-map/
---

{% include bokeh_heading.html %}
<img class="heatmap-static" src="{{site.baseurl}}/assets/img/points-static.png">
<p style="font-size:18px; font-weight:bold; color:gray; text-align:center">Use the categories below to filter the list of protests.</p>
{% include protest-map.html %}
