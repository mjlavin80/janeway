$( document ).ready(function() {

    if (!$("#toc")) {
        return;
    }
    
    var newLine, link, title, linkid, toc, iter, js;

    iter = 0;
    toc = '';

    $("article h2").each(function () {

        link = $(this);
        title = link.html();

        if (!link.attr("id")) {
            link.attr('id', 'heading' + iter.toString());
        }

        linkid = "#" + link.attr("id");

        js = "$('html, body').animate({scrollTop: $( $.attr(this, 'href') ).offset().top - 35}, 500);return false;";

        newLine = "<li><a href='" + linkid + "' onclick=\"" + js + "\">" + title + "</a></li>";

        toc += newLine;
        iter++;

    });

    $("#toc").append(toc);
});