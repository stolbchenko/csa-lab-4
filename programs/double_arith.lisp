(setq a-hi 1)
(setq a-lo -2)

(setq b-hi 0)
(setq b-lo 3)

(setq r-lo (+ a-lo b-lo))

(setq carry 0)
(if (< a-lo 0)
    (if (>= r-lo 0)
        (if (>= b-lo 0)
            (setq carry 1)
            0)
        0)
    0)

(if (>= a-lo 0)
    (if (< b-lo 0)
        (if (>= r-lo 0)
            (setq carry 1)
            0)
        0)
    0)

(setq r-hi (+ (+ a-hi b-hi) carry))

(print "a-hi=")
(print a-hi)
(print " a-lo=")
(print a-lo)
(print "
b-hi=")
(print b-hi)
(print " b-lo=")
(print b-lo)
(print "
sum-hi=")
(print r-hi)
(print " sum-lo=")
(print r-lo)
(print " carry=")
(print carry)
(print-char 10)
