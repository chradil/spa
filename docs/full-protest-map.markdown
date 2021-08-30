---
# Feel free to add content and custom Front Matter to this file.
# To modify the layout, see https://jekyllrb.com/docs/themes/#overriding-theme-defaults

layout: home
title: Protest Map
permalink: /protest-map/
---

{% include bokeh_heading.html %}
<p class="heatmap-static" style="color:gray; text-align:center">This is a static map of all the protests in the <b>School Protests in Africa</b> dataset. To see the interactive map and a full list of types of protests, please visit the desktop version of the website. In the meantime, you can use the categories here to return an abbreviated list of matching protests.</p>
<img class="heatmap-static" src="{{site.baseurl}}/assets/img/points-static.png">
<p class="heatmap-static" style="font-size:18px; font-weight:bold; color:gray; text-align:center">Use the categories below and then click "Filter" to return the list of protests.</p>
{% include protest-map.html %}
