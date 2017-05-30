// http://jeffelmore.org/2011/12/02/easy-anchor-links-that-post-using-jquery/
// fetched on 2012-09-11
// vim: set ts=8 sw=4 sts=4 et ai:

function read_cookie(name) {
    var nameEQ = name + "=";
    var ca = document.cookie.split(';');
    for(var i=0;i < ca.length;i++) {
        var c = ca[i];
        while (c.charAt(0)==' ') c = c.substring(1,c.length);
            if (c.indexOf(nameEQ) == 0) return c.substring(nameEQ.length,c.length);
        }
    return null;
}

(function($) {
$.fn.extend({
    postlink: function(options) {
        var defaults = {'csrf_protected': false};
        options = $.extend(defaults, options);

        return this.each(function() {
            $(this).click(function(e) {
                if (e.isDefaultPrevented()) {
                    // if you add an onclick="return confirm()", things still
                    // work
                    return;
                }
                var frm = $("<form>");
                frm.attr({'action': $(this).attr('href'), 'method': 'post', 'style': 'display:none;'});
                if (options.csrf_protected) {
                    // changed by wjd: added a / before the > for XHTML
                    frm.append("<input type='hidden' name='csrfmiddlewaretoken' value='" + read_cookie('csrftoken') + "'/>");
                }
                frm.appendTo("body");
                frm.submit();
                e.preventDefault();
            });
        });
        }
    });
})(django.jQuery);
